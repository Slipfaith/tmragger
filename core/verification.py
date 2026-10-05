"""Shared data types and parsing helpers for TMX split verification."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass
class VerificationIssue:
    severity: str
    issue_type: str
    message: str
    src_index: int
    tgt_index: int
    suggestion: str


@dataclass
class VerificationRequest:
    src_lang: str
    tgt_lang: str
    original_src: str
    original_tgt: str
    src_parts: list[str]
    tgt_parts: list[str]


@dataclass
class VerificationResult:
    verdict: str
    issues: list[VerificationIssue]
    summary: str
    raw_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Set when the verifier re-cut the split; parts are verbatim slices of the original.
    fixed_src_parts: list[str] | None = None
    fixed_tgt_parts: list[str] | None = None


def render_prompt_template(template: str, verify_request: VerificationRequest) -> str:
    payload = {
        "src_lang": verify_request.src_lang,
        "tgt_lang": verify_request.tgt_lang,
        "original": {
            "src": verify_request.original_src,
            "tgt": verify_request.original_tgt,
        },
        "split_pairs": [
            {"src": src_part, "tgt": tgt_part}
            for src_part, tgt_part in zip(verify_request.src_parts, verify_request.tgt_parts)
        ],
    }
    split_pairs_json = json.dumps(payload["split_pairs"], ensure_ascii=False, indent=2)
    src_parts_json = json.dumps(verify_request.src_parts, ensure_ascii=False, indent=2)
    tgt_parts_json = json.dumps(verify_request.tgt_parts, ensure_ascii=False, indent=2)
    auto_context_json = json.dumps(payload, ensure_ascii=False, indent=2)

    replacements = {
        "{SRC_LANG}": verify_request.src_lang,
        "{TGT_LANG}": verify_request.tgt_lang,
        "{ORIGINAL_SRC}": verify_request.original_src,
        "{ORIGINAL_TGT}": verify_request.original_tgt,
        "{SRC_PARTS_JSON}": src_parts_json,
        "{TGT_PARTS_JSON}": tgt_parts_json,
        "{SPLIT_PAIRS_JSON}": split_pairs_json,
        "{AUTO_CONTEXT_JSON}": auto_context_json,
        "{PAIR_COUNT}": str(len(verify_request.src_parts)),
    }

    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    if "{AUTO_CONTEXT_JSON}" not in template:
        rendered = f"{rendered}\n\nAuto context JSON:\n{auto_context_json}"
    return rendered.strip()


def _parse_verification_json(text: str, raw_text: str) -> VerificationResult:
    data = _try_parse_json_object(text)
    if data is None:
        return VerificationResult(
            verdict="WARN",
            issues=[
                VerificationIssue(
                    severity="medium",
                    issue_type="other",
                    message="Verifier text is not valid JSON.",
                    src_index=0,
                    tgt_index=0,
                    suggestion="Use manual prompt verification.",
                )
            ],
            summary="Invalid verifier JSON payload",
            raw_text=raw_text,
        )

    verdict = str(data.get("verdict", "WARN")).upper()
    if verdict not in {"OK", "WARN", "FAIL"}:
        verdict = "WARN"

    issues_raw = data.get("issues", [])
    issues: list[VerificationIssue] = []
    if isinstance(issues_raw, list):
        for issue in issues_raw:
            if not isinstance(issue, dict):
                continue
            issues.append(
                VerificationIssue(
                    severity=str(issue.get("severity", "low")),
                    issue_type=str(issue.get("type", "other")),
                    message=str(issue.get("message", "")),
                    src_index=int(issue.get("src_index", 0)),
                    tgt_index=int(issue.get("tgt_index", 0)),
                    suggestion=str(issue.get("suggestion", "")),
                )
            )

    summary = str(data.get("summary", "")).strip()
    if not summary:
        summary = "Verification result parsed."
    return VerificationResult(
        verdict=verdict,
        issues=issues,
        summary=summary,
        raw_text=raw_text,
    )


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        obj = json.loads(fenced)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    first = stripped.find("{")
    last = stripped.rfind("}")
    if first == -1 or last <= first:
        return None
    candidate = stripped[first : last + 1]
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        return None
    return None
