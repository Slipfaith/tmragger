"""TMX repair pipeline."""

from __future__ import annotations

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
    GeminiVerificationRequest,
    GeminiVerificationResult,
)
from core.gemini_prompt import GEMINI_CLEANUP_AUDIT_PROMPT
from core.plan import (
    Proposal,
    RepairPlan,
    make_cleanup_proposal_id,
    make_split_proposal_id,
)
from core.reports.html import write_html_diff_report as _write_html_diff_report
from core.reports.xlsx import write_xlsx_multi_sheet_report as _write_xlsx_multi_sheet_report
from core.splitter import build_seg_from_inner_xml, propose_aligned_split, seg_to_inner_xml
from core.tm_cleanup import CleanupOptions, CleanupResult, analyze_and_clean_segments


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
    gemini_input_price_per_1m: float | None = None,
    gemini_output_price_per_1m: float | None = None,
    enable_split: bool = True,
    enable_cleanup_spaces: bool = True,
    enable_cleanup_tag_removal: bool = False,
    enable_cleanup_garbage_removal: bool = True,
    enable_cleanup_warnings: bool = True,
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
        remove_inline_tags=enable_cleanup_tag_removal,
        remove_garbage_segments=enable_cleanup_garbage_removal,
        emit_warnings=enable_cleanup_warnings,
    )

    plan.total_tus = len(tus)
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

    total_tus = len(tus)
    for index, tu in enumerate(tus):
        tu_no = index + 1
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

        original_src_text = seg_to_inner_xml(src_seg)
        original_tgt_text = seg_to_inner_xml(tgt_seg)
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

        if cleanup_result.src_inner_xml != original_src_text:
            _set_seg_inner_xml(src_seg, cleanup_result.src_inner_xml)
        if cleanup_result.tgt_inner_xml != original_tgt_text:
            _set_seg_inner_xml(tgt_seg, cleanup_result.tgt_inner_xml)

        cleaned_src_text = cleanup_result.src_inner_xml
        cleaned_tgt_text = cleanup_result.tgt_inner_xml

        cleanup_gemini_result = _verify_cleanup_with_gemini(
            verify_with_gemini=verify_with_gemini,
            gemini_verifier=gemini_verifier,
            src_lang=tu_src_lang,
            tgt_lang=tu_tgt_lang,
            tu_no=tu_no,
            total_tus=total_tus,
            original_src_text=original_src_text,
            original_tgt_text=original_tgt_text,
            cleaned_src_text=cleaned_src_text,
            cleaned_tgt_text=cleaned_tgt_text,
            cleanup_result=cleanup_result,
            input_price_per_1m=input_price_per_1m,
            output_price_per_1m=output_price_per_1m,
            gemini_checked=gemini_checked,
            gemini_rejected=gemini_rejected,
            gemini_input_tokens=gemini_input_tokens,
            gemini_output_tokens=gemini_output_tokens,
            gemini_total_tokens=gemini_total_tokens,
            progress_callback=progress_callback,
            report_items=report_items,
            gemini_audit_events=gemini_audit_events,
            log=log,
        )
        if cleanup_gemini_result is not None:
            gemini_checked = cleanup_gemini_result["gemini_checked"]
            gemini_rejected = cleanup_gemini_result["gemini_rejected"]
            gemini_input_tokens = cleanup_gemini_result["gemini_input_tokens"]
            gemini_output_tokens = cleanup_gemini_result["gemini_output_tokens"]
            gemini_total_tokens = cleanup_gemini_result["gemini_total_tokens"]
            if cleanup_result.remove_tu and cleanup_gemini_result["verdict"] == "FAIL":
                cleanup_result.remove_tu = False
                cleanup_result.remove_reason = None
                warning_events.append(
                    {
                        "tu_index": index,
                        "tu_no": tu_no,
                        "rule": "cleanup_remove_reverted_by_gemini",
                        "severity": "WARN",
                        "message": "Gemini flagged cleanup removal; TU kept in output.",
                        "src_text": cleanup_result.src_plain_text,
                        "tgt_text": cleanup_result.tgt_plain_text,
                        "details": {},
                    }
                )
                warn_issues_count += 1
                log.info("[TU %s/%s] Cleanup removal reverted by Gemini.", tu_no, total_tus)

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
        if not enable_split:
            skipped_tus += 1
            log.info("[TU %s/%s] Skip: split stage disabled by settings.", tu_no, total_tus)
            _emit_progress(
                progress_callback,
                {
                    "event": "tu_skipped",
                    "tu_index": tu_no,
                    "total_tus": total_tus,
                    "reason": "split_disabled",
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
        proposal = propose_aligned_split(cleaned_src_text, cleaned_tgt_text)
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

        confidence = "HIGH"
        gemini_result: GeminiVerificationResult | None = None
        can_verify = (
            verify_with_gemini
            and gemini_verifier is not None
            and (max_gemini_checks is None or gemini_checked < max_gemini_checks)
        )
        if can_verify:
            gemini_checked += 1
            log.info("[TU %s/%s] Gemini verification started.", tu_no, total_tus)
            verify_request = GeminiVerificationRequest(
                src_lang=tu_src_lang,
                tgt_lang=tu_tgt_lang,
                original_src=cleaned_src_text,
                original_tgt=cleaned_tgt_text,
                src_parts=src_parts,
                tgt_parts=tgt_parts,
            )
            if gemini_prompt_template is not None:
                gemini_result = _run_gemini_verification(
                    gemini_verifier=gemini_verifier,
                    verify_request=verify_request,
                    prompt_template=gemini_prompt_template,
                )
            else:
                gemini_result = _run_gemini_verification(
                    gemini_verifier=gemini_verifier,
                    verify_request=verify_request,
                )

            gemini_input_tokens += max(0, int(gemini_result.prompt_tokens))
            gemini_output_tokens += max(0, int(gemini_result.completion_tokens))
            result_total_tokens = max(
                0,
                int(gemini_result.total_tokens),
            )
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
                continue
            confidence = "MEDIUM"

        split_proposal_id = make_split_proposal_id(index)
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
            continue

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
            "gemini_cleanup_prompt_template": GEMINI_CLEANUP_AUDIT_PROMPT,
            "settings": {
                "enable_split": enable_split,
                "enable_cleanup_spaces": enable_cleanup_spaces,
                "enable_cleanup_tag_removal": enable_cleanup_tag_removal,
                "enable_cleanup_garbage_removal": enable_cleanup_garbage_removal,
                "enable_cleanup_warnings": enable_cleanup_warnings,
            },
            "auto_actions": stats.auto_actions,
            "auto_removed_tus": stats.auto_removed_tus,
            "warn_issues": stats.warn_issues,
            "cleanup_events": cleanup_events,
            "warning_events": warning_events,
            "gemini_audit_events": gemini_audit_events,
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


def _verify_cleanup_with_gemini(
    verify_with_gemini: bool,
    gemini_verifier: object | None,
    src_lang: str,
    tgt_lang: str,
    tu_no: int,
    total_tus: int,
    original_src_text: str,
    original_tgt_text: str,
    cleaned_src_text: str,
    cleaned_tgt_text: str,
    cleanup_result: CleanupResult,
    input_price_per_1m: float,
    output_price_per_1m: float,
    gemini_checked: int,
    gemini_rejected: int,
    gemini_input_tokens: int,
    gemini_output_tokens: int,
    gemini_total_tokens: int,
    progress_callback: Callable[[dict[str, object]], None] | None,
    report_items: list[dict[str, object]],
    gemini_audit_events: list[dict[str, object]],
    log: logging.Logger,
) -> dict[str, object] | None:
    if not verify_with_gemini or gemini_verifier is None:
        return None
    if not bool(getattr(gemini_verifier, "supports_cleanup_audit", False)):
        return None
    if not cleanup_result.auto_actions and not cleanup_result.warnings and not cleanup_result.remove_tu:
        return None

    audit_context = {
        "cleanup_actions": cleanup_result.auto_actions,
        "warnings": cleanup_result.warnings,
        "remove_tu": cleanup_result.remove_tu,
        "remove_reason": cleanup_result.remove_reason,
        "original_src": original_src_text,
        "original_tgt": original_tgt_text,
        "cleaned_src": cleaned_src_text,
        "cleaned_tgt": cleaned_tgt_text,
    }
    verify_request = GeminiVerificationRequest(
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        original_src=json.dumps(audit_context, ensure_ascii=False),
        original_tgt=f"TU {tu_no}/{total_tus} cleanup audit",
        src_parts=[cleaned_src_text],
        tgt_parts=[cleaned_tgt_text],
    )
    gemini_checked += 1
    audit_result = _run_gemini_verification(
        gemini_verifier=gemini_verifier,
        verify_request=verify_request,
        prompt_template=GEMINI_CLEANUP_AUDIT_PROMPT,
    )
    if audit_result.verdict == "FAIL":
        gemini_rejected += 1

    gemini_input_tokens += max(0, int(audit_result.prompt_tokens))
    gemini_output_tokens += max(0, int(audit_result.completion_tokens))
    audit_total_tokens = max(
        0,
        int(audit_result.total_tokens),
    )
    if audit_total_tokens == 0:
        audit_total_tokens = max(0, int(audit_result.prompt_tokens)) + max(
            0,
            int(audit_result.completion_tokens),
        )
    gemini_total_tokens += audit_total_tokens
    run_cost = _estimate_cost_usd(
        gemini_input_tokens,
        gemini_output_tokens,
        input_price_per_1m,
        output_price_per_1m,
    )

    log.info(
        (
            "[TU %s/%s] Gemini cleanup audit verdict=%s issues=%s summary=%s "
            "tokens(in=%s out=%s total=%s) run_tokens(in=%s out=%s total=%s) est_cost=$%.6f"
        ),
        tu_no,
        total_tus,
        audit_result.verdict,
        len(audit_result.issues),
        audit_result.summary,
        audit_result.prompt_tokens,
        audit_result.completion_tokens,
        audit_total_tokens,
        gemini_input_tokens,
        gemini_output_tokens,
        gemini_total_tokens,
        run_cost,
    )
    _emit_progress(
        progress_callback,
        {
            "event": "gemini_cleanup_audit",
            "tu_index": tu_no,
            "total_tus": total_tus,
            "verdict": audit_result.verdict,
            "summary": audit_result.summary,
            "issues_count": len(audit_result.issues),
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
            "category": "cleanup_audit",
            "tu_index": tu_no - 1,
            "verdict": audit_result.verdict,
            "summary": audit_result.summary,
            "issues": [issue.__dict__ for issue in audit_result.issues],
            "prompt_tokens": audit_result.prompt_tokens,
            "completion_tokens": audit_result.completion_tokens,
            "total_tokens": audit_total_tokens,
            "run_gemini_input_tokens": gemini_input_tokens,
            "run_gemini_output_tokens": gemini_output_tokens,
            "run_gemini_total_tokens": gemini_total_tokens,
            "run_estimated_cost_usd": run_cost,
            "cleanup_remove_tu": cleanup_result.remove_tu,
            "cleanup_remove_reason": cleanup_result.remove_reason,
        }
    )
    gemini_audit_events.append(
        {
            "tu_index": tu_no - 1,
            "kind": "cleanup",
            "verdict": audit_result.verdict,
            "summary": audit_result.summary,
            "issues_count": len(audit_result.issues),
            "remove_tu": cleanup_result.remove_tu,
            "remove_reason": cleanup_result.remove_reason,
        }
    )
    return {
        "gemini_checked": gemini_checked,
        "gemini_rejected": gemini_rejected,
        "gemini_input_tokens": gemini_input_tokens,
        "gemini_output_tokens": gemini_output_tokens,
        "gemini_total_tokens": gemini_total_tokens,
        "verdict": audit_result.verdict,
    }


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


