"""Codex CLI verification client for TMX split checks.

Runs split verification through the Codex CLI: ``verify_split`` contract and
the same ``VerificationResult`` shape, so the repair pipeline, cache and
reports keep working unchanged. Each check runs one non-interactive
``codex exec`` call in an empty read-only workspace.

``verify_batch`` checks many split candidates in one call and may return
corrected cut points; such fixes are accepted only when every part is a
verbatim slice of the original text (see ``parts_preserve_text``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from core.verification import (
    VerificationIssue,
    VerificationRequest,
    VerificationResult,
    _parse_verification_json,
    _try_parse_json_object,
    render_prompt_template,
)
from core.verification_prompt import CODEX_BATCH_VERIFICATION_PROMPT, VERIFICATION_PROMPT
from core.splitter import build_seg_from_inner_xml

DEFAULT_CODEX_MODEL = "gpt-6-luna"
DEFAULT_CODEX_REASONING_EFFORT = "medium"
CODEX_REASONING_EFFORTS = ("low", "medium", "high")
DEFAULT_CODEX_BATCH_SIZE = 25

_BATCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["OK", "FIX", "FAIL"]},
                    "src_parts": {"type": "array", "items": {"type": "string"}},
                    "tgt_parts": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["id", "verdict", "src_parts", "tgt_parts", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def find_codex_binary() -> str | None:
    """Locate the codex executable.

    Order: ``CODEX_BIN`` env, the newest binary bundled with the Codex desktop
    app (it tracks the newest models), then ``codex`` on PATH.
    """
    explicit = os.getenv("CODEX_BIN", "").strip()
    if explicit:
        return explicit
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        bundled = list((Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        if bundled:
            return str(max(bundled, key=lambda path: path.stat().st_mtime))
    return shutil.which("codex")


class CodexVerifier:
    """Runs split verification through ``codex exec``."""

    def __init__(
        self,
        model: str = DEFAULT_CODEX_MODEL,
        reasoning_effort: str = DEFAULT_CODEX_REASONING_EFFORT,
        timeout_sec: int = 900,
        prompt_template: str | None = None,
        codex_bin: str | None = None,
        batch_size: int = DEFAULT_CODEX_BATCH_SIZE,
    ):
        resolved_bin = (codex_bin or "").strip() or find_codex_binary()
        if not resolved_bin:
            raise ValueError("Codex CLI not found. Install it or set CODEX_BIN.")
        self.codex_bin = resolved_bin
        self.model = model.strip() or DEFAULT_CODEX_MODEL
        self.reasoning_effort = reasoning_effort.strip() or DEFAULT_CODEX_REASONING_EFFORT
        self.timeout_sec = timeout_sec
        self.prompt_template = prompt_template or VERIFICATION_PROMPT
        self.supports_cleanup_audit = True
        # repair_tmx_file() queues split candidates and calls verify_batch() when > 0.
        self.batch_size = max(0, int(batch_size))
        self.batch_prompt_template = CODEX_BATCH_VERIFICATION_PROMPT
        # Empty sandbox root so the agent never sees the user's files.
        self._workdir = tempfile.mkdtemp(prefix="tmragger-codex-")
        self._schema_path = Path(self._workdir) / "batch-schema.json"
        self._schema_path.write_text(json.dumps(_BATCH_OUTPUT_SCHEMA), encoding="utf-8")

    def build_command(self, output_schema: bool = False) -> list[str]:
        schema_args = ["--output-schema", str(self._schema_path)] if output_schema else []
        return [
            self.codex_bin,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "-s",
            "read-only",
            "-C",
            self._workdir,
            "-m",
            self.model,
            "-c",
            f"model_reasoning_effort={self.reasoning_effort}",
            *schema_args,
            "-",
        ]

    def _run(self, prompt: str, output_schema: bool = False) -> subprocess.CompletedProcess[str]:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(
            self.build_command(output_schema=output_schema),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            creationflags=creationflags,
        )

    def verify_split(
        self,
        verify_request: VerificationRequest,
        prompt_template: str | None = None,
    ) -> VerificationResult:
        active_template = prompt_template if prompt_template is not None else self.prompt_template
        prompt = render_prompt_template(active_template, verify_request)
        try:
            completed = self._run(prompt)
        except Exception as exc:
            return _unavailable_result(f"Codex request failed: {exc}", raw_text=str(exc))

        return parse_codex_jsonl(completed.stdout, stderr=completed.stderr, returncode=completed.returncode)

    def verify_batch(
        self,
        requests: list[VerificationRequest],
        prompt_template: str | None = None,
    ) -> list[VerificationResult]:
        """Verify many split candidates in one Codex call; results follow ``requests`` order.

        ``prompt_template`` replaces the built-in batch prompt; the items are
        appended automatically when it has no ``{ITEMS_JSON}`` placeholder.
        """
        if not requests:
            return []
        items = [
            {
                "id": item_id,
                "src_lang": req.src_lang,
                "tgt_lang": req.tgt_lang,
                "original_src": req.original_src,
                "original_tgt": req.original_tgt,
                "src_parts": req.src_parts,
                "tgt_parts": req.tgt_parts,
            }
            for item_id, req in enumerate(requests)
        ]
        template = prompt_template if prompt_template else self.batch_prompt_template
        if "{ITEMS_JSON}" not in template:
            template = template.rstrip() + "\n\nItems JSON:\n{ITEMS_JSON}"
        prompt = template.replace("{ITEMS_JSON}", json.dumps(items, ensure_ascii=False, indent=1))
        try:
            completed = self._run(prompt, output_schema=True)
        except Exception as exc:
            return [_unavailable_result(f"Codex request failed: {exc}", raw_text=str(exc)) for _ in requests]
        return parse_codex_batch_jsonl(
            completed.stdout,
            requests,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )


def parse_codex_jsonl(stdout: str, stderr: str = "", returncode: int = 0) -> VerificationResult:
    """Turn ``codex exec --json`` event stream into a verification result."""
    text = ""
    error_message = ""
    prompt_tokens = 0
    completion_tokens = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = str(item.get("text", ""))
        elif event_type == "turn.completed":
            usage = event.get("usage") or {}
            prompt_tokens += _safe_int(usage.get("input_tokens"))
            completion_tokens += _safe_int(usage.get("output_tokens")) + _safe_int(
                usage.get("reasoning_output_tokens")
            )
        elif event_type in {"error", "turn.failed"}:
            error_message = str(event.get("message") or (event.get("error") or {}).get("message", ""))

    if not text:
        detail = error_message or stderr.strip()[-500:] or f"exit code {returncode}"
        return _unavailable_result(f"Codex request failed: {detail}", raw_text=stdout or stderr)

    result = _parse_verification_json(text, raw_text=text)
    result.prompt_tokens = prompt_tokens
    result.completion_tokens = completion_tokens
    result.total_tokens = prompt_tokens + completion_tokens
    return result


def parts_preserve_text(original: str, parts: list[str]) -> bool:
    """True when ``parts`` are verbatim, in-order slices covering ``original``.

    Only whitespace may sit between or around the parts, and every part must be
    a well-formed XML fragment (so no cut lands inside a tag).
    """
    if not parts or any(not part.strip() for part in parts):
        return False
    position = 0
    for part in parts:
        found = original.find(part, position)
        if found < 0 or original[position:found].strip():
            return False
        try:
            build_seg_from_inner_xml(part)
        except Exception:
            return False
        position = found + len(part)
    return not original[position:].strip()


def parse_codex_batch_jsonl(
    stdout: str,
    requests: list[VerificationRequest],
    stderr: str = "",
    returncode: int = 0,
) -> list[VerificationResult]:
    envelope = parse_codex_jsonl(stdout, stderr=stderr, returncode=returncode)
    if envelope.summary == "Codex request failed":
        return [_unavailable_result(envelope.issues[0].message, raw_text=envelope.raw_text) for _ in requests]

    data = _try_parse_json_object(envelope.raw_text) or {}
    raw_items = data.get("items")
    by_id: dict[int, dict[str, Any]] = {}
    for entry in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            by_id[int(entry.get("id"))] = entry
        except (TypeError, ValueError):
            continue

    # Usage is reported per call; spread it over items so run totals stay exact.
    count = len(requests)
    results: list[VerificationResult] = []
    for item_id, req in enumerate(requests):
        entry = by_id.get(item_id)
        if entry is None:
            result = _unavailable_result(
                "Codex request failed: item missing in batch response", raw_text=envelope.raw_text
            )
        else:
            result = _batch_entry_to_result(entry, req)
        first = item_id == 0
        result.prompt_tokens = envelope.prompt_tokens // count + (envelope.prompt_tokens % count if first else 0)
        result.completion_tokens = envelope.completion_tokens // count + (
            envelope.completion_tokens % count if first else 0
        )
        result.total_tokens = result.prompt_tokens + result.completion_tokens
        results.append(result)
    return results


def _batch_entry_to_result(entry: dict[str, Any], req: VerificationRequest) -> VerificationResult:
    verdict = str(entry.get("verdict", "")).upper()
    reason = str(entry.get("reason", "")).strip()
    raw_text = json.dumps(entry, ensure_ascii=False)
    if verdict == "OK":
        return VerificationResult(
            verdict="OK", issues=[], summary=reason or "Split approved by Codex.", raw_text=raw_text
        )
    if verdict == "FIX":
        src_parts = [str(part) for part in entry.get("src_parts") or []]
        tgt_parts = [str(part) for part in entry.get("tgt_parts") or []]
        if (
            len(src_parts) == len(tgt_parts) >= 2
            and parts_preserve_text(req.original_src, src_parts)
            and parts_preserve_text(req.original_tgt, tgt_parts)
        ):
            return VerificationResult(
                verdict="WARN",
                issues=[_issue("segmentation", reason or "Cut points corrected by Codex.")],
                summary=f"Split fixed by Codex: {reason}" if reason else "Split fixed by Codex.",
                raw_text=raw_text,
                fixed_src_parts=src_parts,
                fixed_tgt_parts=tgt_parts,
            )
        if _same_cut_points(src_parts, req.src_parts) and _same_cut_points(tgt_parts, req.tgt_parts):
            # Codex kept the proposed cuts and only "corrected" the wording/punctuation:
            # the split itself is confirmed, the text edits are discarded.
            return VerificationResult(
                verdict="OK",
                issues=[],
                summary="Split confirmed by Codex (its text edits were ignored).",
                raw_text=raw_text,
            )
        return VerificationResult(
            verdict="FAIL",
            issues=[_issue("other", "Codex fix rejected: parts do not match the original text verbatim.")],
            summary="Codex fix rejected (text was altered); original TU kept.",
            raw_text=raw_text,
        )
    return VerificationResult(
        verdict="FAIL",
        issues=[_issue("alignment", reason or "Codex rejected the split.")],
        summary=reason or "Split rejected by Codex.",
        raw_text=raw_text,
    )


def _same_cut_points(fixed_parts: list[str], proposed_parts: list[str]) -> bool:
    """True when both lists cut the text in the same places, ignoring punctuation and spacing."""
    if len(fixed_parts) != len(proposed_parts):
        return False
    return all(
        _letters_and_digits(fixed) == _letters_and_digits(proposed)
        for fixed, proposed in zip(fixed_parts, proposed_parts)
    )


def _letters_and_digits(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _issue(issue_type: str, message: str) -> VerificationIssue:
    return VerificationIssue(
        severity="medium",
        issue_type=issue_type,
        message=message,
        src_index=0,
        tgt_index=0,
        suggestion="",
    )


def _unavailable_result(message: str, raw_text: str) -> VerificationResult:
    # "request failed" wording makes repair treat the TU as unverified, not rejected.
    return VerificationResult(
        verdict="WARN",
        issues=[
            VerificationIssue(
                severity="medium",
                issue_type="other",
                message=message,
                src_index=0,
                tgt_index=0,
                suggestion="Retry or use manual verification tab.",
            )
        ],
        summary="Codex request failed",
        raw_text=raw_text,
    )


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return 0
