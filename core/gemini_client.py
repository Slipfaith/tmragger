"""Gemini verification client for TMX split checks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib import error, request

from core.gemini_prompt import GEMINI_VERIFICATION_PROMPT


@dataclass
class GeminiIssue:
    severity: str
    issue_type: str
    message: str
    src_index: int
    tgt_index: int
    suggestion: str


@dataclass
class GeminiVerificationRequest:
    src_lang: str
    tgt_lang: str
    original_src: str
    original_tgt: str
    src_parts: list[str]
    tgt_parts: list[str]


@dataclass
class GeminiVerificationResult:
    verdict: str
    issues: list[GeminiIssue]
    summary: str
    raw_text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class GeminiVerifier:
    """Thin REST client for Gemini generateContent endpoint."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.1-flash-lite-preview",
        timeout_sec: int = 45,
        prompt_template: str | None = None,
    ):
        if not api_key.strip():
            raise ValueError("Gemini API key is empty.")
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-3.1-flash-lite-preview"
        self.timeout_sec = timeout_sec
        self.prompt_template = prompt_template or GEMINI_VERIFICATION_PROMPT
        self.supports_cleanup_audit = True

    def verify_split(
        self,
        verify_request: GeminiVerificationRequest,
        prompt_template: str | None = None,
    ) -> GeminiVerificationResult:
        active_template = prompt_template if prompt_template is not None else self.prompt_template
        prompt = render_prompt_template(active_template, verify_request)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0},
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            return GeminiVerificationResult(
                verdict="WARN",
                issues=[
                    GeminiIssue(
                        severity="medium",
                        issue_type="other",
                        message=f"Gemini HTTP error: {exc.code}",
                        src_index=0,
                        tgt_index=0,
                        suggestion="Retry or use manual verification tab.",
                    )
                ],
                summary="Gemini HTTP error",
                raw_text=str(exc),
            )
        except Exception as exc:  # pragma: no cover
            return GeminiVerificationResult(
                verdict="WARN",
                issues=[
                    GeminiIssue(
                        severity="medium",
                        issue_type="other",
                        message=f"Gemini request failed: {exc}",
                        src_index=0,
                        tgt_index=0,
                        suggestion="Retry or use manual verification tab.",
                    )
                ],
                summary="Gemini request failed",
                raw_text=str(exc),
            )

        return parse_gemini_response(raw)


def render_prompt_template(template: str, verify_request: GeminiVerificationRequest) -> str:
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


def parse_gemini_response(raw_json_text: str) -> GeminiVerificationResult:
    try:
        parsed = json.loads(raw_json_text)
    except json.JSONDecodeError:
        return GeminiVerificationResult(
            verdict="WARN",
            issues=[
                GeminiIssue(
                    severity="medium",
                    issue_type="other",
                    message="Gemini did not return valid JSON envelope.",
                    src_index=0,
                    tgt_index=0,
                    suggestion="Use manual prompt verification.",
                )
            ],
            summary="Invalid Gemini response envelope",
            raw_text=raw_json_text,
        )

    prompt_tokens, completion_tokens, total_tokens = _extract_usage_tokens(parsed)
    text = _extract_text_from_generate_content(parsed)
    if not text:
        return GeminiVerificationResult(
            verdict="WARN",
            issues=[
                GeminiIssue(
                    severity="medium",
                    issue_type="other",
                    message="Gemini response has no text candidate.",
                    src_index=0,
                    tgt_index=0,
                    suggestion="Use manual prompt verification.",
                )
            ],
            summary="No candidate text",
            raw_text=raw_json_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    result = _parse_verification_json(text, raw_text=text)
    result.prompt_tokens = prompt_tokens
    result.completion_tokens = completion_tokens
    result.total_tokens = total_tokens
    return result


def _extract_text_from_generate_content(data: dict[str, Any]) -> str:
    candidates = data.get("candidates", [])
    if not candidates:
        return ""
    first_candidate = candidates[0] if isinstance(candidates, list) else {}
    content = first_candidate.get("content", {})
    parts = content.get("parts", [])
    if not parts:
        return ""
    text_chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and "text" in part:
            text_chunks.append(str(part["text"]))
    return "\n".join(text_chunks).strip()


def _extract_usage_tokens(data: dict[str, Any]) -> tuple[int, int, int]:
    usage = data.get("usageMetadata", {})
    if not isinstance(usage, dict):
        return 0, 0, 0

    prompt_tokens = _safe_int(usage.get("promptTokenCount", 0))
    completion_tokens = _safe_int(usage.get("candidatesTokenCount", 0))
    total_tokens = _safe_int(usage.get("totalTokenCount", prompt_tokens + completion_tokens))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _parse_verification_json(text: str, raw_text: str) -> GeminiVerificationResult:
    data = _try_parse_json_object(text)
    if data is None:
        return GeminiVerificationResult(
            verdict="WARN",
            issues=[
                GeminiIssue(
                    severity="medium",
                    issue_type="other",
                    message="Gemini text is not valid JSON.",
                    src_index=0,
                    tgt_index=0,
                    suggestion="Use manual prompt verification.",
                )
            ],
            summary="Invalid Gemini JSON payload",
            raw_text=raw_text,
        )

    verdict = str(data.get("verdict", "WARN")).upper()
    if verdict not in {"OK", "WARN", "FAIL"}:
        verdict = "WARN"

    issues_raw = data.get("issues", [])
    issues: list[GeminiIssue] = []
    if isinstance(issues_raw, list):
        for issue in issues_raw:
            if not isinstance(issue, dict):
                continue
            issues.append(
                GeminiIssue(
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
        summary = "Gemini verification result parsed."
    return GeminiVerificationResult(
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
