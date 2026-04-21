"""TMX repair pipeline."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Callable
import xml.etree.ElementTree as ET

from core.diff import (
    preview as _preview,
    render_inline_diff as _render_inline_diff,
    visible_text as _visible_text,
    whitespace_delta_summary as _whitespace_delta_summary,
)
from core.events import (
    CleanupProposedEvent,
    FileCompleteEvent,
    FileStartEvent,
    GeminiResultEvent,
    GeminiUsage,
    RepairEvent,
    SplitProposedEvent,
    TuSkippedEvent,
    TuStartEvent,
    WarningEvent,
)
from core.gemini_client import (
    GeminiIssue,
    GeminiVerificationRequest,
    GeminiVerificationResult,
)
from core.plan import (
    Proposal,
    RepairPlan,
    make_cleanup_proposal_id,
    make_split_proposal_id,
)
from core.reports.html import write_html_diff_report as _write_html_diff_report
from core.reports.xlsx import write_xlsx_multi_sheet_report as _write_xlsx_multi_sheet_report
from core.splitter import build_seg_from_inner_xml, propose_aligned_split, seg_to_inner_xml
from core.tm_cleanup import (
    CleanupOptions,
    CleanupResult,
    analyze_and_clean_segments,
    clean_service_markup_text,
)


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
ET.register_namespace("xml", "http://www.w3.org/XML/1998/namespace")

# <prop> types whose values reference neighbouring TUs (pre/post context, concordance,
# character offsets, etc.). After a split such props become misleading or outright wrong
# for every produced TU, so we drop them from the new split TUs. Matching is
# case-insensitive and covers both the canonical "x-Context*" SDL/Trados variants and a
# handful of common CAT-tool equivalents.
SPLIT_DROPPED_PROP_TYPES: frozenset[str] = frozenset(
    {
        "x-context",
        "x-contextpre",
        "x-contextpost",
        "x-contextcontent",
        "x-prev-segment",
        "x-next-segment",
        "x-previous-segment",
        "x-following-segment",
        "x-concordance",
        "x-sdl-contextpre",
        "x-sdl-contextpost",
    }
)


def _prop_type(elem: ET.Element) -> str:
    return (elem.attrib.get("type") or "").strip().lower()


def _should_drop_prop_in_split(elem: ET.Element) -> bool:
    if _local_name(elem.tag) != "prop":
        return False
    return _prop_type(elem) in SPLIT_DROPPED_PROP_TYPES


@dataclass
class RepairStats:
    total_tus: int
    split_tus: int
    created_tus: int
    src_lang: str
    tgt_lang: str | None
    skipped_tus: int
    high_confidence_splits: int = 0
    medium_confidence_splits: int = 0
    gemini_checked: int = 0
    gemini_rejected: int = 0
    gemini_input_tokens: int = 0
    gemini_output_tokens: int = 0
    gemini_total_tokens: int = 0
    gemini_estimated_cost_usd: float = 0.0
    auto_actions: int = 0
    auto_removed_tus: int = 0
    warn_issues: int = 0
    # Populated for every run; in plan mode the pipeline short-circuits after
    # building this. `None` only if the pipeline never reached the stats stage.
    plan: RepairPlan | None = None


DEFAULT_GEMINI_INPUT_PRICE_PER_1M_USD = 0.10
DEFAULT_GEMINI_OUTPUT_PRICE_PER_1M_USD = 0.40


class RepairControlInterrupt(RuntimeError):
    """Raised by UI control callbacks (pause/stop) to interrupt processing."""


def repair_tmx_file(
    input_path: Path,
    output_path: Path,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
    verify_with_gemini: bool = False,
    gemini_verifier: object | None = None,
    max_gemini_checks: int | None = None,
    report_path: Path | None = None,
    gemini_prompt_template: str | None = None,
    html_report_path: Path | None = None,
    xlsx_report_path: Path | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    event_callback: Callable[[RepairEvent], None] | None = None,
    mode: str = "apply",
    accepted_split_ids: set[str] | None = None,
    accepted_cleanup_ids: set[str] | None = None,
    preverified_split_confidence_by_id: dict[str, str] | None = None,
    preverified_split_verdict_by_id: dict[str, str] | None = None,
    gemini_max_parallel: int = 1,
    resume_state_path: Path | None = None,
    gemini_cache_path: Path | None = None,
    checkpoint_every_tus: int = 50,
    gemini_input_price_per_1m: float | None = None,
    gemini_output_price_per_1m: float | None = None,
    enable_split: bool = True,
    enable_split_short_sentence_pair_guard: bool = False,
    enable_cleanup_spaces: bool = True,
    enable_cleanup_percent_wrapped: bool = False,
    enable_cleanup_game_markup: bool = True,
    enable_cleanup_tag_removal: bool = False,
    enable_cleanup_garbage_removal: bool = True,
    enable_cleanup_warnings: bool = True,
    enable_dedup_tus: bool = False,
) -> RepairStats:
    """Repair a TMX file by splitting aligned bilingual segments.

    Parameters
    ----------
    mode:
        ``"apply"`` (default) runs the full pipeline and writes the output TMX
        plus any configured reports. ``"plan"`` performs analysis only,
        populates ``RepairStats.plan`` with every candidate edit, and writes
        nothing — use this for a preview/approval UI. In plan mode the
        returned plan is serializable via :meth:`RepairPlan.to_json`.
    event_callback:
        Optional typed-event stream (see ``core.events``). Fired alongside the
        legacy ``progress_callback`` dict stream.
    accepted_split_ids / accepted_cleanup_ids:
        When provided, only proposals with matching IDs are applied. Proposals
        outside the set are skipped (for splits: TU is left untouched; for
        cleanup: the raw text remains as-is). Use ``None`` for "apply all".
    """
    log = logger or logging.getLogger("tmx_repair")
    gemini_max_parallel = max(1, int(gemini_max_parallel))
    checkpoint_every_tus = max(1, int(checkpoint_every_tus))
    if resume_state_path is not None and gemini_max_parallel > 1:
        log.info(
            "Resume/checkpoint mode enabled; forcing gemini_max_parallel=1 for deterministic checkpoints."
        )
        gemini_max_parallel = 1
    plan_mode = mode == "plan"
    if mode not in {"apply", "plan"}:
        raise ValueError(f"Unknown mode: {mode!r}; expected 'apply' or 'plan'.")
    plan = RepairPlan(input_path=str(input_path))
    tree = ET.parse(input_path)
    root = tree.getroot()

    header = root.find("header")
    src_lang = header.attrib.get("srclang", "") if header is not None else ""

    body = root.find("body")
    if body is None:
        raise ValueError("TMX body is missing.")

    tus = list(body.findall("tu"))
    replacement_map: dict[int, list[ET.Element]] = {}
    split_tus = 0
    skipped_tus = 0
    tgt_lang_seen: str | None = None
    high_confidence_splits = 0
    medium_confidence_splits = 0
    gemini_checked = 0
    gemini_rejected = 0
    report_items: list[dict[str, object]] = []
    split_events: list[dict[str, object]] = []
    cleanup_events: list[dict[str, object]] = []
    warning_events: list[dict[str, object]] = []
    gemini_audit_events: list[dict[str, object]] = []
    active_prompt_template_for_run = gemini_prompt_template
    gemini_input_tokens = 0
    gemini_output_tokens = 0
    gemini_total_tokens = 0
    # In-run memoization for deterministic Gemini split verification.
    # Repeated identical split candidates are common in localization TMX and
    # can safely reuse verdicts because prompt + payload are identical.
    gemini_verification_cache: dict[
        tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], str],
        GeminiVerificationResult,
    ] = {}
    gemini_cache_dirty = False
    pending_verification_events: list[dict[str, object]] = []
    start_index = 0
    processed_since_checkpoint = 0
    auto_actions_count = 0
    auto_removed_tus = 0
    warn_issues_count = 0
    # Pricing priority: explicit kwarg > env var (legacy) > default constant.
    # The env-var path is kept so existing scripts keep working; new callers
    # should pass prices directly.
    if gemini_input_price_per_1m is not None:
        input_price_per_1m = max(0.0, float(gemini_input_price_per_1m))
    else:
        input_price_per_1m = _read_env_float(
            "GEMINI_PRICE_INPUT_PER_1M_USD",
            DEFAULT_GEMINI_INPUT_PRICE_PER_1M_USD,
        )
    if gemini_output_price_per_1m is not None:
        output_price_per_1m = max(0.0, float(gemini_output_price_per_1m))
    else:
        output_price_per_1m = _read_env_float(
            "GEMINI_PRICE_OUTPUT_PER_1M_USD",
            DEFAULT_GEMINI_OUTPUT_PRICE_PER_1M_USD,
        )
    cleanup_options = CleanupOptions(
        normalize_spaces=enable_cleanup_spaces,
        remove_percent_wrapped_tokens=enable_cleanup_percent_wrapped,
        remove_game_markup=enable_cleanup_game_markup,
        remove_inline_tags=enable_cleanup_tag_removal,
        remove_garbage_segments=enable_cleanup_garbage_removal,
        emit_warnings=enable_cleanup_warnings,
    )
    accepted_split_tu_indexes: set[int] | None = None
    accepted_cleanup_tu_indexes: set[int] | None = None
    if not plan_mode and accepted_split_ids is not None:
        accepted_split_tu_indexes = _extract_split_tu_indexes(accepted_split_ids)
    if not plan_mode and accepted_cleanup_ids is not None:
        accepted_cleanup_tu_indexes = _extract_cleanup_tu_indexes(accepted_cleanup_ids)

    if gemini_cache_path is not None:
        loaded_cache = _load_gemini_cache(gemini_cache_path, log)
        if loaded_cache:
            gemini_verification_cache.update(loaded_cache)

    if resume_state_path is not None and not plan_mode:
        resume_state = _load_resume_state(resume_state_path, log)
        if resume_state is not None and _resume_state_matches(
            resume_state=resume_state,
            input_path=input_path,
            output_path=output_path,
            total_tus=len(tus),
        ):
            start_index = max(0, min(int(resume_state.get("next_tu_index", 0) or 0), len(tus)))
            replacement_map = _deserialize_replacement_map(resume_state.get("replacement_map", {}))
            split_tus = int(resume_state.get("split_tus", split_tus) or split_tus)
            skipped_tus = int(resume_state.get("skipped_tus", skipped_tus) or skipped_tus)
            high_confidence_splits = int(
                resume_state.get("high_confidence_splits", high_confidence_splits) or high_confidence_splits
            )
            medium_confidence_splits = int(
                resume_state.get("medium_confidence_splits", medium_confidence_splits) or medium_confidence_splits
            )
            gemini_checked = int(resume_state.get("gemini_checked", gemini_checked) or gemini_checked)
            gemini_rejected = int(resume_state.get("gemini_rejected", gemini_rejected) or gemini_rejected)
            gemini_input_tokens = int(resume_state.get("gemini_input_tokens", gemini_input_tokens) or gemini_input_tokens)
            gemini_output_tokens = int(
                resume_state.get("gemini_output_tokens", gemini_output_tokens) or gemini_output_tokens
            )
            gemini_total_tokens = int(resume_state.get("gemini_total_tokens", gemini_total_tokens) or gemini_total_tokens)
            auto_actions_count = int(resume_state.get("auto_actions", auto_actions_count) or auto_actions_count)
            auto_removed_tus = int(resume_state.get("auto_removed_tus", auto_removed_tus) or auto_removed_tus)
            warn_issues_count = int(resume_state.get("warn_issues", warn_issues_count) or warn_issues_count)
            report_items = list(resume_state.get("report_items", report_items))
            split_events = list(resume_state.get("split_events", split_events))
            cleanup_events = list(resume_state.get("cleanup_events", cleanup_events))
            warning_events = list(resume_state.get("warning_events", warning_events))
            gemini_audit_events = list(resume_state.get("gemini_audit_events", gemini_audit_events))
            pending_verification_events = list(
                resume_state.get("pending_verification_events", pending_verification_events)
            )
            log.info(
                "Resume loaded: %s (next_tu_index=%s, restored_replacements=%s).",
                resume_state_path,
                start_index,
                len(replacement_map),
            )

    plan.total_tus = len(tus)

    dedup_skip_indexes: set[int] = set()
    if enable_dedup_tus:
        _seen_pairs: set[tuple[str, str]] = set()
        for _i, _tu_elem in enumerate(tus):
            _segs = [
                _s
                for _tuv in _tu_elem
                if _local_name(_tuv.tag) == "tuv"
                for _s in _tuv
                if _local_name(_s.tag) == "seg"
            ]
            if len(_segs) != 2:
                continue
            _pair = (seg_to_inner_xml(_segs[0]), seg_to_inner_xml(_segs[1]))
            if _pair in _seen_pairs:
                dedup_skip_indexes.add(_i)
            else:
                _seen_pairs.add(_pair)

    _emit_progress(
        progress_callback,
        {
            "event": "file_start",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "total_tus": len(tus),
            "src_lang": src_lang,
            "verify_with_gemini": verify_with_gemini and gemini_verifier is not None,
            "gemini_input_tokens": 0,
            "gemini_output_tokens": 0,
            "gemini_total_tokens": 0,
            "gemini_estimated_cost_usd": 0.0,
        },
    )
    _emit_event(
        event_callback,
        FileStartEvent(
            input_path=str(input_path),
            total_tus=len(tus),
            src_lang=src_lang,
        ),
    )

    if verify_with_gemini and gemini_verifier is not None:
        active_template = active_prompt_template_for_run
        if active_template is None:
            active_template = getattr(gemini_verifier, "prompt_template", None)
        if not active_template:
            active_template = "<EMPTY_PROMPT_TEMPLATE>"
        active_prompt_template_for_run = active_template
        log.info("Gemini prompt template in use:\n%s", active_template)
        log.info(
            "Gemini estimated pricing: input=$%.4f/1M tokens, output=$%.4f/1M tokens",
            input_price_per_1m,
            output_price_per_1m,
        )

    gemini_executor: ThreadPoolExecutor | None = None
    pending_parallel_checks: list[dict[str, object]] = []

    def _finalize_split_candidate(
        *,
        index: int,
        tu: ET.Element,
        tu_no: int,
        total_tus: int,
        tu_src_lang: str,
        tu_tgt_lang: str,
        cleaned_src_text: str,
        cleaned_tgt_text: str,
        src_parts: list[str],
        tgt_parts: list[str],
        split_proposal_id: str,
        base_confidence: str,
        gemini_result: GeminiVerificationResult | None,
        force_medium_confidence: bool,
    ) -> None:
        nonlocal gemini_input_tokens
        nonlocal gemini_output_tokens
        nonlocal gemini_total_tokens
        nonlocal gemini_rejected
        nonlocal skipped_tus
        nonlocal split_tus
        nonlocal high_confidence_splits
        nonlocal medium_confidence_splits

        confidence = "MEDIUM" if force_medium_confidence else base_confidence

        if gemini_result is not None:
            if _is_gemini_unavailable(gemini_result):
                skipped_tus += 1
                pending_entry = {
                    "tu_index": index,
                    "reason": gemini_result.summary,
                    "src_lang": tu_src_lang,
                    "tgt_lang": tu_tgt_lang,
                    "original_src": cleaned_src_text,
                    "original_tgt": cleaned_tgt_text,
                    "src_parts": list(src_parts),
                    "tgt_parts": list(tgt_parts),
                }
                pending_verification_events.append(pending_entry)
                report_items.append(
                    {
                        "category": "split_verification_pending",
                        "tu_index": index,
                        "summary": gemini_result.summary,
                        "issues": [issue.__dict__ for issue in gemini_result.issues],
                        "src_parts": list(src_parts),
                        "tgt_parts": list(tgt_parts),
                    }
                )
                log.warning(
                    "[TU %s/%s] Gemini unavailable (%s). Marked as pending; original TU kept.",
                    tu_no,
                    total_tus,
                    gemini_result.summary,
                )
                _emit_progress(
                    progress_callback,
                    {
                        "event": "tu_rejected",
                        "tu_index": tu_no,
                        "total_tus": total_tus,
                        "reason": "gemini_unavailable",
                        "split_tus": split_tus,
                        "skipped_tus": skipped_tus,
                        "gemini_checked": gemini_checked,
                        "gemini_rejected": gemini_rejected,
                        "gemini_input_tokens": gemini_input_tokens,
                        "gemini_output_tokens": gemini_output_tokens,
                        "gemini_total_tokens": gemini_total_tokens,
                        "gemini_estimated_cost_usd": _estimate_cost_usd(
                            gemini_input_tokens,
                            gemini_output_tokens,
                            input_price_per_1m,
                            output_price_per_1m,
                        ),
                    },
                )
                return
            gemini_input_tokens += max(0, int(gemini_result.prompt_tokens))
            gemini_output_tokens += max(0, int(gemini_result.completion_tokens))
            result_total_tokens = max(0, int(gemini_result.total_tokens))
            if result_total_tokens == 0:
                result_total_tokens = max(0, int(gemini_result.prompt_tokens)) + max(
                    0,
                    int(gemini_result.completion_tokens),
                )
            gemini_total_tokens += result_total_tokens
            run_cost = _estimate_cost_usd(
                gemini_input_tokens,
                gemini_output_tokens,
                input_price_per_1m,
                output_price_per_1m,
            )

            log.info(
                (
                    "[TU %s/%s] Gemini verdict=%s issues=%s summary=%s "
                    "tokens(in=%s out=%s total=%s) run_tokens(in=%s out=%s total=%s) est_cost=$%.6f"
                ),
                tu_no,
                total_tus,
                gemini_result.verdict,
                len(gemini_result.issues),
                gemini_result.summary,
                gemini_result.prompt_tokens,
                gemini_result.completion_tokens,
                result_total_tokens,
                gemini_input_tokens,
                gemini_output_tokens,
                gemini_total_tokens,
                run_cost,
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "gemini_result",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "verdict": gemini_result.verdict,
                    "summary": gemini_result.summary,
                    "issues_count": len(gemini_result.issues),
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": run_cost,
                },
            )
            report_items.append(
                {
                    "category": "split_verification",
                    "tu_index": index,
                    "verdict": gemini_result.verdict,
                    "summary": gemini_result.summary,
                    "issues": [issue.__dict__ for issue in gemini_result.issues],
                    "prompt_tokens": gemini_result.prompt_tokens,
                    "completion_tokens": gemini_result.completion_tokens,
                    "total_tokens": result_total_tokens,
                    "run_gemini_input_tokens": gemini_input_tokens,
                    "run_gemini_output_tokens": gemini_output_tokens,
                    "run_gemini_total_tokens": gemini_total_tokens,
                    "run_estimated_cost_usd": run_cost,
                }
            )
            gemini_audit_events.append(
                {
                    "tu_index": index,
                    "kind": "split",
                    "verdict": gemini_result.verdict,
                    "summary": gemini_result.summary,
                    "issues_count": len(gemini_result.issues),
                }
            )
            if gemini_result.verdict == "FAIL":
                gemini_rejected += 1
                skipped_tus += 1
                log.info("[TU %s/%s] Split rejected by Gemini. Keeping original TU.", tu_no, total_tus)
                _emit_progress(
                    progress_callback,
                    {
                        "event": "tu_rejected",
                        "tu_index": tu_no,
                        "total_tus": total_tus,
                        "reason": "gemini_fail",
                        "split_tus": split_tus,
                        "skipped_tus": skipped_tus,
                        "gemini_checked": gemini_checked,
                        "gemini_rejected": gemini_rejected,
                        "gemini_input_tokens": gemini_input_tokens,
                        "gemini_output_tokens": gemini_output_tokens,
                        "gemini_total_tokens": gemini_total_tokens,
                        "gemini_estimated_cost_usd": run_cost,
                    },
                )
                return

        split_accepted_by_user = (
            accepted_split_ids is None or split_proposal_id in accepted_split_ids
        )
        plan.proposals.append(
            Proposal(
                proposal_id=split_proposal_id,
                kind="split",
                tu_index=index,
                accepted=split_accepted_by_user,
                confidence=confidence,
                gemini_verdict=(gemini_result.verdict if gemini_result is not None else ""),
                src_parts=list(src_parts),
                tgt_parts=list(tgt_parts),
                original_src=cleaned_src_text,
                original_tgt=cleaned_tgt_text,
            )
        )
        _emit_event(
            event_callback,
            SplitProposedEvent(
                tu_index=index,
                src_parts=list(src_parts),
                tgt_parts=list(tgt_parts),
                confidence=confidence,  # type: ignore[arg-type]
                original_src=cleaned_src_text,
                original_tgt=cleaned_tgt_text,
            ),
        )

        if not split_accepted_by_user:
            skipped_tus += 1
            log.info(
                "[TU %s/%s] Split rejected by caller (accepted_split_ids filter).",
                tu_no,
                total_tus,
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_rejected",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "user_rejected",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            return

        replacement_map[index] = _build_split_tus(
            tu=tu,
            src_lang=tu_src_lang,
            tgt_lang=tu_tgt_lang,
            src_parts=src_parts,
            tgt_parts=tgt_parts,
            confidence=confidence,
            gemini_result=gemini_result,
        )
        split_tus += 1
        split_events.append(
            {
                "tu_index": index,
                "confidence": confidence,
                "gemini_verdict": gemini_result.verdict if gemini_result is not None else None,
                "original_src": cleaned_src_text,
                "original_tgt": cleaned_tgt_text,
                "src_parts": src_parts,
                "tgt_parts": tgt_parts,
            }
        )
        if confidence == "HIGH":
            high_confidence_splits += 1
        else:
            medium_confidence_splits += 1
        log.info(
            "[TU %s/%s] Split accepted. confidence=%s, output_parts=%s",
            tu_no,
            total_tus,
            confidence,
            len(src_parts),
        )
        _emit_progress(
            progress_callback,
            {
                "event": "tu_split_applied",
                "tu_index": tu_no,
                "total_tus": total_tus,
                "confidence": confidence,
                "split_tus": split_tus,
                "skipped_tus": skipped_tus,
                "gemini_checked": gemini_checked,
                "gemini_rejected": gemini_rejected,
                "gemini_input_tokens": gemini_input_tokens,
                "gemini_output_tokens": gemini_output_tokens,
                "gemini_total_tokens": gemini_total_tokens,
                "gemini_estimated_cost_usd": _estimate_cost_usd(
                    gemini_input_tokens,
                    gemini_output_tokens,
                    input_price_per_1m,
                    output_price_per_1m,
                ),
            },
        )

    def _drain_one_pending_check() -> None:
        nonlocal gemini_cache_dirty
        if not pending_parallel_checks:
            return
        item = pending_parallel_checks.pop(0)
        future = item["future"]
        assert isinstance(future, Future)
        try:
            result = future.result()
        except Exception as exc:
            result = GeminiVerificationResult(
                verdict="WARN",
                issues=[],
                summary=f"Gemini request failed in worker thread: {exc}",
            )
        assert isinstance(result, GeminiVerificationResult)
        gemini_verification_cache[item["cache_key"]] = result
        gemini_cache_dirty = True
        _finalize_split_candidate(
            index=int(item["index"]),
            tu=item["tu"],  # type: ignore[arg-type]
            tu_no=int(item["tu_no"]),
            total_tus=total_tus,
            tu_src_lang=str(item["tu_src_lang"]),
            tu_tgt_lang=str(item["tu_tgt_lang"]),
            cleaned_src_text=str(item["cleaned_src_text"]),
            cleaned_tgt_text=str(item["cleaned_tgt_text"]),
            src_parts=list(item["src_parts"]),  # type: ignore[arg-type]
            tgt_parts=list(item["tgt_parts"]),  # type: ignore[arg-type]
            split_proposal_id=str(item["split_proposal_id"]),
            base_confidence=str(item["base_confidence"]),
            gemini_result=result,
            force_medium_confidence=True,
        )

    def _write_resume_checkpoint(next_tu_index: int) -> None:
        if resume_state_path is None or plan_mode:
            return
        state = {
            "version": 1,
            "input_path": str(input_path.resolve()),
            "output_path": str(output_path.resolve()),
            "total_tus": len(tus),
            "next_tu_index": max(0, min(int(next_tu_index), len(tus))),
            "split_tus": split_tus,
            "skipped_tus": skipped_tus,
            "high_confidence_splits": high_confidence_splits,
            "medium_confidence_splits": medium_confidence_splits,
            "gemini_checked": gemini_checked,
            "gemini_rejected": gemini_rejected,
            "gemini_input_tokens": gemini_input_tokens,
            "gemini_output_tokens": gemini_output_tokens,
            "gemini_total_tokens": gemini_total_tokens,
            "auto_actions": auto_actions_count,
            "auto_removed_tus": auto_removed_tus,
            "warn_issues": warn_issues_count,
            "replacement_map": _serialize_replacement_map(replacement_map),
            "report_items": report_items,
            "split_events": split_events,
            "cleanup_events": cleanup_events,
            "warning_events": warning_events,
            "gemini_audit_events": gemini_audit_events,
            "pending_verification_events": pending_verification_events,
        }
        _save_resume_state(path=resume_state_path, state=state, logger=log)

    total_tus = len(tus)
    process_indexes: list[int] = list(range(start_index, len(tus)))
    if (
        not plan_mode
        and accepted_split_tu_indexes is not None
        and accepted_cleanup_tu_indexes is not None
    ):
        selected_union = sorted(accepted_split_tu_indexes | accepted_cleanup_tu_indexes)
        process_indexes = [idx for idx in selected_union if start_index <= idx < len(tus)]
        log.info(
            "Apply fast-path enabled: processing selected TUs only (%s of %s).",
            len(process_indexes),
            len(tus),
        )
    for processed_pos, index in enumerate(process_indexes):
        tu = tus[index]
        tu_no = index + 1
        if resume_state_path is not None and not plan_mode and processed_pos > 0:
            processed_since_checkpoint += 1
            if processed_since_checkpoint >= checkpoint_every_tus:
                _write_resume_checkpoint(next_tu_index=index)
                processed_since_checkpoint = 0
        _emit_progress(
            progress_callback,
            {
                "event": "tu_start",
                "tu_index": tu_no,
                "total_tus": total_tus,
                "split_tus": split_tus,
                "skipped_tus": skipped_tus,
                "gemini_checked": gemini_checked,
                "gemini_rejected": gemini_rejected,
                "gemini_input_tokens": gemini_input_tokens,
                "gemini_output_tokens": gemini_output_tokens,
                "gemini_total_tokens": gemini_total_tokens,
                "gemini_estimated_cost_usd": _estimate_cost_usd(
                    gemini_input_tokens,
                    gemini_output_tokens,
                    input_price_per_1m,
                    output_price_per_1m,
                ),
            },
        )
        _emit_event(event_callback, TuStartEvent(tu_index=index, total_tus=total_tus))
        log.info("[TU %s/%s] Analyze started.", tu_no, total_tus)
        split_selected_for_tu = (
            accepted_split_tu_indexes is None or index in accepted_split_tu_indexes
        )
        cleanup_selected_for_tu = (
            accepted_cleanup_tu_indexes is None or index in accepted_cleanup_tu_indexes
        )
        if not plan_mode and not split_selected_for_tu and not cleanup_selected_for_tu:
            skipped_tus += 1
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "not_selected_for_apply",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        tuv_elements = [child for child in list(tu) if _local_name(child.tag) == "tuv"]
        if len(tuv_elements) < 2:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: less than 2 TUV entries.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "less_than_two_tuv",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        if len(tuv_elements) > 2:
            tuv_langs = [_get_tuv_lang(t) for t in tuv_elements]
            skipped_tus += 1
            warning_events.append(
                {
                    "tu_index": index,
                    "tu_no": tu_no,
                    "rule": "multilang_tu_skipped",
                    "severity": "WARN",
                    "message": (
                        "TU has more than 2 <tuv> entries "
                        f"(languages: {', '.join(tuv_langs)}); left unchanged."
                    ),
                    "src_text": "",
                    "tgt_text": "",
                    "details": {"langs": tuv_langs, "count": len(tuv_elements)},
                }
            )
            warn_issues_count += 1
            log.info(
                "[TU %s/%s] Skip: multi-language TU with %s TUVs (%s).",
                tu_no,
                total_tus,
                len(tuv_elements),
                ", ".join(tuv_langs),
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "multilang_tu",
                    "tuv_count": len(tuv_elements),
                    "tuv_langs": tuv_langs,
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        src_tuv = _find_tuv_by_lang(tuv_elements, src_lang)
        if src_tuv is None and len(tuv_elements) == 2:
            # Some TMX files use srclang that does not exactly match tuv xml:lang
            # (for example, "en" vs "en-US" or missing srclang). In that case,
            # keep processing by falling back to the first TUV.
            src_tuv = tuv_elements[0]
            log.info(
                "[TU %s/%s] Source TUV for '%s' not found; fallback to first TUV '%s'.",
                tu_no,
                total_tus,
                src_lang,
                _get_tuv_lang(src_tuv),
            )
        if src_tuv is None:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: source TUV for '%s' not found.", tu_no, total_tus, src_lang)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "missing_src_tuv",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        tgt_tuv = next((t for t in tuv_elements if t is not src_tuv), None)
        if tgt_tuv is None:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: target TUV not found.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "missing_tgt_tuv",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        tu_src_lang = _get_tuv_lang(src_tuv) or src_lang
        tgt_lang_seen = _get_tuv_lang(tgt_tuv)
        tu_tgt_lang = tgt_lang_seen
        src_seg = src_tuv.find("seg")
        tgt_seg = tgt_tuv.find("seg")
        if src_seg is None or tgt_seg is None:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: source or target SEG is missing.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "missing_seg",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        if enable_dedup_tus and index in dedup_skip_indexes:
            auto_removed_tus += 1
            skipped_tus += 1
            replacement_map[index] = []
            if plan_mode:
                plan.proposals.append(
                    Proposal(
                        proposal_id=make_cleanup_proposal_id(index, "dedup_tu", 0),
                        kind="cleanup",
                        tu_index=index,
                        rule="dedup_tu",
                        message="Дубль TU: идентичный сегмент уже встречался в файле.",
                        original_src=seg_to_inner_xml(src_seg),
                        original_tgt=seg_to_inner_xml(tgt_seg),
                    )
                )
            log.info("[TU %s/%s] AUTO removed as duplicate.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_removed_cleanup",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "dedup_tu",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        original_src_text = seg_to_inner_xml(src_seg)
        original_tgt_text = seg_to_inner_xml(tgt_seg)
        if not plan_mode and not cleanup_selected_for_tu:
            cleanup_result = CleanupResult(
                src_inner_xml=original_src_text,
                tgt_inner_xml=original_tgt_text,
                src_plain_text="".join(build_seg_from_inner_xml(original_src_text).itertext()).strip(),
                tgt_plain_text="".join(build_seg_from_inner_xml(original_tgt_text).itertext()).strip(),
                auto_actions=[],
                warnings=[],
                remove_tu=False,
                remove_reason=None,
            )
        else:
            cleanup_result = analyze_and_clean_segments(
                src_inner_xml=original_src_text,
                tgt_inner_xml=original_tgt_text,
                src_lang=tu_src_lang,
                tgt_lang=tu_tgt_lang,
                options=cleanup_options,
            )
        auto_actions_count += len(cleanup_result.auto_actions)
        warn_issues_count += len(cleanup_result.warnings)

        for ordinal, action in enumerate(cleanup_result.auto_actions):
            rule_name = str(action.get("rule", ""))
            action_event = {
                "tu_index": index,
                "tu_no": tu_no,
                "scope": "segment",
                "rule": rule_name,
                "message": action.get("message", ""),
                "before_src": action.get("before_src", original_src_text),
                "after_src": action.get("after_src", cleanup_result.src_inner_xml),
                "before_tgt": action.get("before_tgt", original_tgt_text),
                "after_tgt": action.get("after_tgt", cleanup_result.tgt_inner_xml),
                "remove_reason": action.get("remove_reason"),
            }
            cleanup_events.append(action_event)
            cleanup_proposal_id = make_cleanup_proposal_id(index, rule_name, ordinal)
            plan.proposals.append(
                Proposal(
                    proposal_id=cleanup_proposal_id,
                    kind="cleanup",
                    tu_index=index,
                    rule=rule_name,
                    message=str(action.get("message", "")),
                    before_src=str(action_event["before_src"]),
                    after_src=str(action_event["after_src"]),
                    before_tgt=str(action_event["before_tgt"]),
                    after_tgt=str(action_event["after_tgt"]),
                    original_src=original_src_text,
                    original_tgt=original_tgt_text,
                )
            )
            _emit_event(
                event_callback,
                CleanupProposedEvent(
                    tu_index=index,
                    rule=rule_name,
                    message=str(action.get("message", "")),
                    before_src=str(action_event["before_src"]),
                    after_src=str(action_event["after_src"]),
                    before_tgt=str(action_event["before_tgt"]),
                    after_tgt=str(action_event["after_tgt"]),
                ),
            )
            log.info(
                "[TU %s/%s] AUTO %s: %s",
                tu_no,
                total_tus,
                action_event["rule"],
                action_event["message"],
            )

        for warning in cleanup_result.warnings:
            warning_event = {
                "tu_index": index,
                "tu_no": tu_no,
                "rule": warning.get("rule", ""),
                "severity": warning.get("severity", "WARN"),
                "message": warning.get("message", ""),
                "src_text": cleanup_result.src_plain_text,
                "tgt_text": cleanup_result.tgt_plain_text,
                "details": warning,
            }
            warning_events.append(warning_event)
            _emit_event(
                event_callback,
                WarningEvent(
                    tu_index=index,
                    rule=str(warning.get("rule", "")),
                    severity=str(warning.get("severity", "WARN")),
                    message=str(warning.get("message", "")),
                ),
            )
            log.info(
                "[TU %s/%s] WARN %s: %s",
                tu_no,
                total_tus,
                warning_event["rule"],
                warning_event["message"],
            )

        # x-ContextContent often duplicates segment text for CAT context.
        # Keep it aligned with service-markup cleanup so "leftovers" do not
        # appear in output TMX metadata.
        if enable_cleanup_percent_wrapped or enable_cleanup_game_markup:
            context_content_props = [
                child
                for child in list(tu)
                if _local_name(child.tag) == "prop" and _prop_type(child) == "x-contextcontent"
            ]
            for context_prop in context_content_props:
                before_context = context_prop.text or ""
                after_context, applied_context_rules = clean_service_markup_text(
                    before_context,
                    remove_percent_wrapped_tokens=enable_cleanup_percent_wrapped,
                    remove_game_markup=enable_cleanup_game_markup,
                )
                if after_context == before_context:
                    continue
                context_prop.text = after_context
                for context_rule in applied_context_rules:
                    auto_actions_count += 1
                    if context_rule == "remove_percent_wrapped_tokens":
                        context_message = (
                            "x-ContextContent cleaned: removed conservative %token% placeholders."
                        )
                    else:
                        context_message = (
                            "x-ContextContent cleaned: removed game markup "
                            "(^{...}^, $m(...|...), &lt;Color=...&gt;...&lt;/Color&gt;)."
                        )
                    rule_name = f"context_{context_rule}"
                    action_event = {
                        "tu_index": index,
                        "tu_no": tu_no,
                        "scope": "context_content",
                        "rule": rule_name,
                        "message": context_message,
                        "before_src": before_context,
                        "after_src": after_context,
                        "before_tgt": "",
                        "after_tgt": "",
                        "remove_reason": None,
                    }
                    cleanup_events.append(action_event)
                    _emit_event(
                        event_callback,
                        CleanupProposedEvent(
                            tu_index=index,
                            rule=rule_name,
                            message=context_message,
                            before_src=before_context,
                            after_src=after_context,
                            before_tgt="",
                            after_tgt="",
                        ),
                    )
                    log.info(
                        "[TU %s/%s] AUTO %s: %s",
                        tu_no,
                        total_tus,
                        rule_name,
                        context_message,
                    )

        if cleanup_result.src_inner_xml != original_src_text:
            _set_seg_inner_xml(src_seg, cleanup_result.src_inner_xml)
        if cleanup_result.tgt_inner_xml != original_tgt_text:
            _set_seg_inner_xml(tgt_seg, cleanup_result.tgt_inner_xml)

        cleaned_src_text = cleanup_result.src_inner_xml
        cleaned_tgt_text = cleanup_result.tgt_inner_xml

        # Cleanup is deterministic/rule-based by design and does not require
        # Gemini verification. Gemini is used only for split-checks.

        if cleanup_result.remove_tu:
            auto_removed_tus += 1
            skipped_tus += 1
            replacement_map[index] = []
            log.info(
                "[TU %s/%s] AUTO removed as garbage: %s",
                tu_no,
                total_tus,
                cleanup_result.remove_reason or "unknown",
            )
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_removed_cleanup",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": cleanup_result.remove_reason or "cleanup_remove",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        log.info(
            "[TU %s/%s] Candidate text | src=%s | tgt=%s",
            tu_no,
            total_tus,
            _preview(cleaned_src_text),
            _preview(cleaned_tgt_text),
        )
        if not enable_split or (not plan_mode and not split_selected_for_tu):
            skipped_tus += 1
            split_skip_reason = "split_disabled" if enable_split is False else "split_not_selected"
            log.info("[TU %s/%s] Skip: split stage not selected.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": split_skip_reason,
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue
        proposal = propose_aligned_split(
            cleaned_src_text,
            cleaned_tgt_text,
            enable_short_sentence_pair_guard=enable_split_short_sentence_pair_guard,
        )
        if proposal is None:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: no aligned split proposal.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "no_split_proposal",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        src_parts, tgt_parts = proposal
        if len(src_parts) < 2:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: proposal produced < 2 parts.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "proposal_less_than_two_parts",
                    "split_tus": split_tus,
                    "skipped_tus": skipped_tus,
                    "gemini_checked": gemini_checked,
                    "gemini_rejected": gemini_rejected,
                    "gemini_input_tokens": gemini_input_tokens,
                    "gemini_output_tokens": gemini_output_tokens,
                    "gemini_total_tokens": gemini_total_tokens,
                    "gemini_estimated_cost_usd": _estimate_cost_usd(
                        gemini_input_tokens,
                        gemini_output_tokens,
                        input_price_per_1m,
                        output_price_per_1m,
                    ),
                },
            )
            continue

        log.info("[TU %s/%s] Split proposal found: %s parts.", tu_no, total_tus, len(src_parts))
        for pair_index, (src_part, tgt_part) in enumerate(zip(src_parts, tgt_parts), start=1):
            log.info(
                "[TU %s/%s] Pair %s | src=%s | tgt=%s",
                tu_no,
                total_tus,
                pair_index,
                _preview(src_part),
                _preview(tgt_part),
            )

        split_proposal_id = make_split_proposal_id(index)
        preverified_confidence = (
            (preverified_split_confidence_by_id or {}).get(split_proposal_id, "").strip().upper()
        )
        if preverified_confidence not in {"HIGH", "MEDIUM"}:
            preverified_confidence = ""
        preverified_verdict = (
            (preverified_split_verdict_by_id or {}).get(split_proposal_id, "").strip().upper()
        )
        if preverified_verdict not in {"OK", "WARN", "FAIL"}:
            preverified_verdict = ""

        base_confidence = preverified_confidence or "HIGH"
        gemini_result: GeminiVerificationResult | None = None
        force_medium_confidence = False
        if preverified_verdict:
            gemini_result = GeminiVerificationResult(
                verdict=preverified_verdict,
                issues=[],
                summary="Reused plan-phase Gemini verdict.",
            )
        can_verify = (
            verify_with_gemini
            and gemini_verifier is not None
            and (max_gemini_checks is None or gemini_checked < max_gemini_checks)
            and not preverified_confidence
            and not preverified_verdict
        )
        if can_verify:
            verify_request = GeminiVerificationRequest(
                src_lang=tu_src_lang,
                tgt_lang=tu_tgt_lang,
                original_src=cleaned_src_text,
                original_tgt=cleaned_tgt_text,
                src_parts=src_parts,
                tgt_parts=tgt_parts,
            )
            cache_key = (
                tu_src_lang,
                tu_tgt_lang,
                cleaned_src_text,
                cleaned_tgt_text,
                tuple(src_parts),
                tuple(tgt_parts),
                active_prompt_template_for_run or "",
            )
            cached_result = gemini_verification_cache.get(cache_key)
            if cached_result is not None:
                gemini_result = GeminiVerificationResult(
                    verdict=cached_result.verdict,
                    issues=list(cached_result.issues),
                    summary=f"{cached_result.summary} (cache hit)",
                    raw_text=cached_result.raw_text,
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                )
                log.info("[TU %s/%s] Gemini verification reused from cache.", tu_no, total_tus)
                force_medium_confidence = True
            elif gemini_max_parallel > 1:
                if gemini_executor is None:
                    gemini_executor = ThreadPoolExecutor(max_workers=gemini_max_parallel)
                while len(pending_parallel_checks) >= gemini_max_parallel:
                    _drain_one_pending_check()
                gemini_checked += 1
                log.info(
                    "[TU %s/%s] Gemini verification queued (parallel=%s).",
                    tu_no,
                    total_tus,
                    gemini_max_parallel,
                )
                pending_parallel_checks.append(
                    {
                        "future": gemini_executor.submit(
                            _run_gemini_verification,
                            gemini_verifier=gemini_verifier,
                            verify_request=verify_request,
                            prompt_template=gemini_prompt_template,
                        ),
                        "cache_key": cache_key,
                        "index": index,
                        "tu": tu,
                        "tu_no": tu_no,
                        "tu_src_lang": tu_src_lang,
                        "tu_tgt_lang": tu_tgt_lang,
                        "cleaned_src_text": cleaned_src_text,
                        "cleaned_tgt_text": cleaned_tgt_text,
                        "src_parts": list(src_parts),
                        "tgt_parts": list(tgt_parts),
                        "split_proposal_id": split_proposal_id,
                        "base_confidence": base_confidence,
                    }
                )
                continue
            else:
                gemini_checked += 1
                log.info("[TU %s/%s] Gemini verification started.", tu_no, total_tus)
                gemini_result = _run_gemini_verification(
                    gemini_verifier=gemini_verifier,
                    verify_request=verify_request,
                    prompt_template=gemini_prompt_template,
                )
                gemini_verification_cache[cache_key] = gemini_result
                gemini_cache_dirty = True
                force_medium_confidence = True

        _finalize_split_candidate(
            index=index,
            tu=tu,
            tu_no=tu_no,
            total_tus=total_tus,
            tu_src_lang=tu_src_lang,
            tu_tgt_lang=tu_tgt_lang,
            cleaned_src_text=cleaned_src_text,
            cleaned_tgt_text=cleaned_tgt_text,
            src_parts=src_parts,
            tgt_parts=tgt_parts,
            split_proposal_id=split_proposal_id,
            base_confidence=base_confidence,
            gemini_result=gemini_result,
            force_medium_confidence=force_medium_confidence,
        )

    while pending_parallel_checks:
        _drain_one_pending_check()
    if gemini_executor is not None:
        gemini_executor.shutdown(wait=True)
    if resume_state_path is not None and not plan_mode:
        _write_resume_checkpoint(next_tu_index=len(tus))
    if gemini_cache_path is not None and gemini_cache_dirty:
        _save_gemini_cache(
            path=gemini_cache_path,
            cache=gemini_verification_cache,
            logger=log,
        )

    if replacement_map:
        body.clear()
        for index, tu in enumerate(tus):
            replacements = replacement_map.get(index)
            if replacements is not None:
                body.extend(replacements)
            else:
                body.append(tu)

    created_tus = len(list(body.findall("tu")))
    gemini_estimated_cost_usd = _estimate_cost_usd(
        gemini_input_tokens,
        gemini_output_tokens,
        input_price_per_1m,
        output_price_per_1m,
    )
    stats = RepairStats(
        total_tus=len(tus),
        split_tus=split_tus,
        created_tus=created_tus,
        src_lang=src_lang,
        tgt_lang=tgt_lang_seen,
        skipped_tus=skipped_tus,
        high_confidence_splits=high_confidence_splits,
        medium_confidence_splits=medium_confidence_splits,
        gemini_checked=gemini_checked,
        gemini_rejected=gemini_rejected,
        gemini_input_tokens=gemini_input_tokens,
        gemini_output_tokens=gemini_output_tokens,
        gemini_total_tokens=gemini_total_tokens,
        gemini_estimated_cost_usd=gemini_estimated_cost_usd,
        auto_actions=auto_actions_count,
        auto_removed_tus=auto_removed_tus,
        warn_issues=warn_issues_count,
    )
    log.info(
        (
            "TMX processed: total=%s, split=%s, skipped=%s, output_tu=%s, "
            "high=%s, medium=%s, gemini_checked=%s, gemini_rejected=%s, "
            "gemini_tokens_in=%s, gemini_tokens_out=%s, gemini_tokens_total=%s, est_cost=$%.6f, "
            "auto_actions=%s, auto_removed_tus=%s, warn_issues=%s"
        ),
        stats.total_tus,
        stats.split_tus,
        stats.skipped_tus,
        stats.created_tus,
        stats.high_confidence_splits,
        stats.medium_confidence_splits,
        stats.gemini_checked,
        stats.gemini_rejected,
        stats.gemini_input_tokens,
        stats.gemini_output_tokens,
        stats.gemini_total_tokens,
        stats.gemini_estimated_cost_usd,
        stats.auto_actions,
        stats.auto_removed_tus,
        stats.warn_issues,
    )
    stats.plan = plan
    _emit_progress(
        progress_callback,
        {
            "event": "file_complete",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "total_tus": stats.total_tus,
            "split_tus": stats.split_tus,
            "skipped_tus": stats.skipped_tus,
            "output_tu": stats.created_tus,
            "high_confidence_splits": stats.high_confidence_splits,
            "medium_confidence_splits": stats.medium_confidence_splits,
            "gemini_checked": stats.gemini_checked,
            "gemini_rejected": stats.gemini_rejected,
            "gemini_input_tokens": stats.gemini_input_tokens,
            "gemini_output_tokens": stats.gemini_output_tokens,
            "gemini_total_tokens": stats.gemini_total_tokens,
            "gemini_estimated_cost_usd": stats.gemini_estimated_cost_usd,
            "auto_actions": stats.auto_actions,
            "auto_removed_tus": stats.auto_removed_tus,
            "warn_issues": stats.warn_issues,
        },
    )
    _emit_event(
        event_callback,
        FileCompleteEvent(
            input_path=str(input_path),
            output_path=str(output_path),
            total_tus=stats.total_tus,
            split_tus=stats.split_tus,
            created_tus=stats.created_tus,
            skipped_tus=stats.skipped_tus,
        ),
    )

    if plan_mode:
        log.info("Plan mode: no output or reports written; %d proposals collected.", len(plan.proposals))
        return stats

    if not dry_run:
        tree.write(output_path, encoding="utf-8", xml_declaration=True, short_empty_elements=False)
        log.info("Saved repaired TMX to %s", output_path)
    else:
        log.info("Dry run enabled. Output file was not written.")

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "input_path": str(input_path),
            "output_path": str(output_path),
            "total_tus": stats.total_tus,
            "split_tus": stats.split_tus,
            "created_tus": stats.created_tus,
            "skipped_tus": stats.skipped_tus,
            "high_confidence_splits": stats.high_confidence_splits,
            "medium_confidence_splits": stats.medium_confidence_splits,
            "gemini_checked": stats.gemini_checked,
            "gemini_rejected": stats.gemini_rejected,
            "gemini_input_tokens": stats.gemini_input_tokens,
            "gemini_output_tokens": stats.gemini_output_tokens,
            "gemini_total_tokens": stats.gemini_total_tokens,
            "gemini_estimated_cost_usd": stats.gemini_estimated_cost_usd,
            "gemini_pricing_input_per_1m_usd": input_price_per_1m,
            "gemini_pricing_output_per_1m_usd": output_price_per_1m,
            "gemini_prompt_template": active_prompt_template_for_run,
            "gemini_cleanup_prompt_template": None,
            "gemini_cleanup_audit_enabled": False,
            "settings": {
                "enable_split": enable_split,
                "enable_split_short_sentence_pair_guard": enable_split_short_sentence_pair_guard,
                "enable_cleanup_spaces": enable_cleanup_spaces,
                "enable_cleanup_percent_wrapped": enable_cleanup_percent_wrapped,
                "enable_cleanup_game_markup": enable_cleanup_game_markup,
                "enable_cleanup_tag_removal": enable_cleanup_tag_removal,
                "enable_cleanup_garbage_removal": enable_cleanup_garbage_removal,
                "enable_cleanup_warnings": enable_cleanup_warnings,
                "enable_dedup_tus": enable_dedup_tus,
            },
            "auto_actions": stats.auto_actions,
            "auto_removed_tus": stats.auto_removed_tus,
            "warn_issues": stats.warn_issues,
            "cleanup_events": cleanup_events,
            "warning_events": warning_events,
            "gemini_audit_events": gemini_audit_events,
            "pending_verification_events": pending_verification_events,
            "items": report_items,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Verification report saved to %s", report_path)

    if html_report_path is not None:
        html_report_path.parent.mkdir(parents=True, exist_ok=True)
        _write_html_diff_report(
            path=html_report_path,
            input_path=input_path,
            output_path=output_path,
            stats=stats,
            split_events=split_events,
            cleanup_events=cleanup_events,
            warning_events=warning_events,
            gemini_audit_events=gemini_audit_events,
        )
        log.info("HTML diff report saved to %s", html_report_path)

    if xlsx_report_path is not None:
        xlsx_report_path.parent.mkdir(parents=True, exist_ok=True)
        _write_xlsx_multi_sheet_report(
            path=xlsx_report_path,
            input_path=input_path,
            output_path=output_path,
            stats=stats,
            split_events=split_events,
            cleanup_events=cleanup_events,
            warning_events=warning_events,
            gemini_audit_events=gemini_audit_events,
        )
        log.info("XLSX multi-sheet report saved to %s", xlsx_report_path)

    return stats


def _emit_progress(
    progress_callback: Callable[[dict[str, object]], None] | None,
    payload: dict[str, object],
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except RepairControlInterrupt:
        raise
    except Exception:
        # Progress callbacks are optional and must never break the repair run.
        return


def _emit_event(
    event_callback: Callable[[RepairEvent], None] | None,
    event: RepairEvent,
) -> None:
    if event_callback is None:
        return
    try:
        event_callback(event)
    except RepairControlInterrupt:
        raise
    except Exception:
        # Typed-event listeners are optional and must never break the run.
        return


def _read_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    return value if value >= 0 else default


def _estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    input_price_per_1m: float,
    output_price_per_1m: float,
) -> float:
    return (max(0, input_tokens) / 1_000_000.0) * input_price_per_1m + (
        max(0, output_tokens) / 1_000_000.0
    ) * output_price_per_1m


def _set_seg_inner_xml(seg: ET.Element, inner_xml: str) -> None:
    replacement_seg = build_seg_from_inner_xml(inner_xml)
    seg.clear()
    seg.text = replacement_seg.text
    for child in list(replacement_seg):
        seg.append(deepcopy(child))


def _build_split_tus(
    tu: ET.Element,
    src_lang: str,
    tgt_lang: str,
    src_parts: list[str],
    tgt_parts: list[str],
    confidence: str,
    gemini_result: GeminiVerificationResult | None,
) -> list[ET.Element]:
    parts_count = len(src_parts)
    split_tus: list[ET.Element] = []

    for idx in range(parts_count):
        new_tu = ET.Element("tu", dict(tu.attrib))
        confidence_prop = ET.Element("prop", {"type": "x-TMXRepair-Confidence"})
        confidence_prop.text = confidence
        new_tu.append(confidence_prop)
        if gemini_result is not None:
            verdict_prop = ET.Element("prop", {"type": "x-TMXRepair-GeminiVerdict"})
            verdict_prop.text = gemini_result.verdict
            new_tu.append(verdict_prop)

        for child in list(tu):
            if _local_name(child.tag) != "tuv":
                if _should_drop_prop_in_split(child):
                    continue
                new_tu.append(deepcopy(child))
                continue

            lang = _get_tuv_lang(child)
            original_seg = child.find("seg")
            if original_seg is None:
                continue

            new_tuv = ET.Element("tuv", dict(child.attrib))
            if lang == src_lang:
                seg_inner = src_parts[idx]
            elif lang == tgt_lang:
                seg_inner = tgt_parts[idx]
            else:
                seg_inner = seg_to_inner_xml(original_seg)

            new_tuv.append(build_seg_from_inner_xml(seg_inner))
            new_tu.append(new_tuv)

        split_tus.append(new_tu)
    return split_tus


def _find_tuv_by_lang(tuv_elements: list[ET.Element], lang: str) -> ET.Element | None:
    if not tuv_elements:
        return None

    target = _normalize_lang_tag(lang)
    if not target:
        return None

    exact = next(
        (tuv for tuv in tuv_elements if _normalize_lang_tag(_get_tuv_lang(tuv)) == target),
        None,
    )
    if exact is not None:
        return exact

    target_base = _lang_base(target)
    if not target_base:
        return None
    primary_matches = [
        tuv for tuv in tuv_elements if _lang_base(_normalize_lang_tag(_get_tuv_lang(tuv))) == target_base
    ]
    if not primary_matches:
        return None
    # Deterministic fallback when multiple variants exist (e.g. en-US/en-GB):
    # with only 2-language TUs this still picks a stable source side.
    return primary_matches[0]


def _get_tuv_lang(tuv: ET.Element) -> str:
    return tuv.attrib.get(XML_LANG) or tuv.attrib.get("lang") or ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_lang_tag(lang: str) -> str:
    return (lang or "").strip().replace("_", "-").lower()


def _lang_base(lang: str) -> str:
    normalized = _normalize_lang_tag(lang)
    if not normalized:
        return ""
    return normalized.split("-", 1)[0]


def _extract_split_tu_indexes(split_ids: set[str]) -> set[int]:
    indexes: set[int] = set()
    for proposal_id in split_ids:
        if not proposal_id.startswith("split:"):
            continue
        try:
            indexes.add(int(proposal_id.split(":", 1)[1]))
        except (TypeError, ValueError):
            continue
    return indexes


def _extract_cleanup_tu_indexes(cleanup_ids: set[str]) -> set[int]:
    indexes: set[int] = set()
    for proposal_id in cleanup_ids:
        if not proposal_id.startswith("cleanup:"):
            continue
        parts = proposal_id.split(":", 3)
        if len(parts) < 2:
            continue
        try:
            indexes.add(int(parts[1]))
        except (TypeError, ValueError):
            continue
    return indexes


def _is_gemini_unavailable(result: GeminiVerificationResult) -> bool:
    if result.verdict != "WARN":
        return False
    summary = (result.summary or "").lower()
    if "request failed" in summary or "http error" in summary or "no candidate text" in summary:
        return True
    for issue in result.issues:
        message = (issue.message or "").lower()
        if "request failed" in message or "http error" in message:
            return True
    return False


def _serialize_replacement_map(replacement_map: dict[int, list[ET.Element]]) -> dict[str, list[str]]:
    serialized: dict[str, list[str]] = {}
    for index, elements in replacement_map.items():
        serialized[str(index)] = [
            ET.tostring(elem, encoding="unicode", method="xml", short_empty_elements=False)
            for elem in elements
        ]
    return serialized


def _deserialize_replacement_map(raw: object) -> dict[int, list[ET.Element]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[int, list[ET.Element]] = {}
    for key, value in raw.items():
        try:
            index = int(key)
        except Exception:
            continue
        if not isinstance(value, list):
            continue
        parsed: list[ET.Element] = []
        for xml_text in value:
            if not isinstance(xml_text, str):
                continue
            try:
                parsed.append(ET.fromstring(xml_text))
            except ET.ParseError:
                continue
        result[index] = parsed
    return result


def _save_resume_state(path: Path, state: dict[str, object], logger: logging.Logger) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:
        logger.warning("Failed to save resume checkpoint %s: %s", path, exc)


def _load_resume_state(path: Path, logger: logging.Logger) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read resume checkpoint %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _resume_state_matches(
    *,
    resume_state: dict[str, object],
    input_path: Path,
    output_path: Path,
    total_tus: int,
) -> bool:
    input_value = str(resume_state.get("input_path", ""))
    output_value = str(resume_state.get("output_path", ""))
    state_total_tus = int(resume_state.get("total_tus", total_tus) or total_tus)
    if input_value != str(input_path.resolve()):
        return False
    if output_value != str(output_path.resolve()):
        return False
    if state_total_tus != total_tus:
        return False
    return True


def _cache_key_to_string(
    key: tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], str],
) -> str:
    return json.dumps(
        {
            "src_lang": key[0],
            "tgt_lang": key[1],
            "original_src": key[2],
            "original_tgt": key[3],
            "src_parts": list(key[4]),
            "tgt_parts": list(key[5]),
            "prompt": key[6],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _cache_key_from_string(
    encoded: str,
) -> tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], str] | None:
    try:
        data = json.loads(encoded)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    src_parts = data.get("src_parts", [])
    tgt_parts = data.get("tgt_parts", [])
    if not isinstance(src_parts, list) or not isinstance(tgt_parts, list):
        return None
    return (
        str(data.get("src_lang", "")),
        str(data.get("tgt_lang", "")),
        str(data.get("original_src", "")),
        str(data.get("original_tgt", "")),
        tuple(str(item) for item in src_parts),
        tuple(str(item) for item in tgt_parts),
        str(data.get("prompt", "")),
    )


def _save_gemini_cache(
    *,
    path: Path,
    cache: dict[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], str], GeminiVerificationResult],
    logger: logging.Logger,
) -> None:
    payload: dict[str, object] = {"version": 1, "entries": {}}
    entries: dict[str, object] = {}
    for key, result in cache.items():
        entries[_cache_key_to_string(key)] = {
            "verdict": result.verdict,
            "issues": [issue.__dict__ for issue in result.issues],
            "summary": result.summary,
        }
    payload["entries"] = entries
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:
        logger.warning("Failed to save Gemini cache %s: %s", path, exc)


def _load_gemini_cache(
    path: Path,
    logger: logging.Logger,
) -> dict[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], str], GeminiVerificationResult]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load Gemini cache %s: %s", path, exc)
        return {}
    entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
    if not isinstance(entries, dict):
        return {}
    loaded: dict[tuple[str, str, str, str, tuple[str, ...], tuple[str, ...], str], GeminiVerificationResult] = {}
    for encoded_key, raw_result in entries.items():
        if not isinstance(encoded_key, str) or not isinstance(raw_result, dict):
            continue
        key = _cache_key_from_string(encoded_key)
        if key is None:
            continue
        verdict = str(raw_result.get("verdict", "WARN")).upper()
        if verdict not in {"OK", "WARN", "FAIL"}:
            verdict = "WARN"
        issues_raw = raw_result.get("issues", [])
        issues = []
        if isinstance(issues_raw, list):
            for issue_raw in issues_raw:
                if isinstance(issue_raw, dict):
                    issues.append(
                        GeminiIssue(
                            severity=str(issue_raw.get("severity", "medium")),
                            issue_type=str(issue_raw.get("issue_type", "other")),
                            message=str(issue_raw.get("message", "")),
                            src_index=int(issue_raw.get("src_index", 0) or 0),
                            tgt_index=int(issue_raw.get("tgt_index", 0) or 0),
                            suggestion=str(issue_raw.get("suggestion", "")),
                        )
                    )
        loaded[key] = GeminiVerificationResult(
            verdict=verdict,
            issues=issues,
            summary=str(raw_result.get("summary", "Cached verdict")),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
    return loaded


def _run_gemini_verification(
    gemini_verifier: object,
    verify_request: GeminiVerificationRequest,
    prompt_template: str | None = None,
) -> GeminiVerificationResult:
    verify_method = getattr(gemini_verifier, "verify_split", None)
    if verify_method is None:
        raise ValueError("gemini_verifier must have verify_split(request) method.")
    if prompt_template is None:
        result = verify_method(verify_request)
    else:
        try:
            result = verify_method(verify_request, prompt_template=prompt_template)
        except TypeError:
            result = verify_method(verify_request)
    if not isinstance(result, GeminiVerificationResult):
        raise ValueError("verify_split(request) must return GeminiVerificationResult.")
    return result


