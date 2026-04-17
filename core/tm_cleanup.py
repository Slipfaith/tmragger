"""TM cleanup rules for AUTO normalization/removal and WARN diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import re
from typing import Any
import unicodedata

from core.splitter import build_seg_from_inner_xml


_TAG_RE = re.compile(r"(<[^>]+>)")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_CJK_RE = re.compile(r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF]")

_CYRILLIC_LANGS = {
    "ru",
    "uk",
    "be",
    "bg",
    "mk",
    "sr",
    "kk",
    "ky",
    "tg",
    "tt",
}
_CJK_LANGS = {"zh", "ja", "ko"}
_FORCE_SPACE_AFTER = set(".!?:;)]}\"'")
_NO_SPACE_BEFORE = set(",.;:!?)]}\"'")
_NO_SPACE_AFTER = set("([{\"'")


@dataclass
class CleanupResult:
    src_inner_xml: str
    tgt_inner_xml: str
    src_plain_text: str
    tgt_plain_text: str
    auto_actions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    remove_tu: bool = False
    remove_reason: str | None = None


@dataclass(frozen=True)
class CleanupOptions:
    normalize_spaces: bool = True
    remove_inline_tags: bool = False
    remove_garbage_segments: bool = True
    emit_warnings: bool = True


def analyze_and_clean_segments(
    src_inner_xml: str,
    tgt_inner_xml: str,
    src_lang: str,
    tgt_lang: str,
    options: CleanupOptions | None = None,
) -> CleanupResult:
    opts = options or CleanupOptions()
    original_src = src_inner_xml or ""
    original_tgt = tgt_inner_xml or ""
    cleaned_src = original_src
    cleaned_tgt = original_tgt
    auto_actions: list[dict[str, Any]] = []

    if opts.remove_inline_tags:
        tag_cleaned_src = _strip_inline_tags_inner_xml(cleaned_src)
        tag_cleaned_tgt = _strip_inline_tags_inner_xml(cleaned_tgt)
        if tag_cleaned_src != cleaned_src or tag_cleaned_tgt != cleaned_tgt:
            auto_actions.append(
                {
                    "rule": "remove_inline_tags",
                    "message": "Inline tags removed; boundary spaces repaired to avoid text glue.",
                    "before_src": cleaned_src,
                    "after_src": tag_cleaned_src,
                    "before_tgt": cleaned_tgt,
                    "after_tgt": tag_cleaned_tgt,
                }
            )
            cleaned_src = tag_cleaned_src
            cleaned_tgt = tag_cleaned_tgt

    if opts.normalize_spaces:
        space_cleaned_src = _normalize_inner_xml(cleaned_src)
        space_cleaned_tgt = _normalize_inner_xml(cleaned_tgt)
        if space_cleaned_src != cleaned_src or space_cleaned_tgt != cleaned_tgt:
            auto_actions.append(
                {
                    "rule": "normalize_spaces",
                    "message": "ASCII spaces normalized: collapsed doubles and trimmed edges.",
                    "before_src": cleaned_src,
                    "after_src": space_cleaned_src,
                    "before_tgt": cleaned_tgt,
                    "after_tgt": space_cleaned_tgt,
                }
            )
            cleaned_src = space_cleaned_src
            cleaned_tgt = space_cleaned_tgt

    src_plain = _plain_text(cleaned_src)
    tgt_plain = _plain_text(cleaned_tgt)
    warnings: list[dict[str, Any]] = []

    remove_tu = False
    remove_reason: str | None = None
    if opts.remove_garbage_segments:
        if _is_numeric_only(src_plain) and _is_numeric_only(tgt_plain):
            remove_tu = True
            remove_reason = "numeric_only_both"
        elif _source_has_meaning(src_plain) and not _text_has_letters(tgt_plain) and not _text_has_digits(tgt_plain):
            remove_tu = True
            remove_reason = "target_missing_letters"
        elif _is_punctuation_or_empty(src_plain) and _is_punctuation_or_empty(tgt_plain):
            remove_tu = True
            remove_reason = "punctuation_or_tags_only"

    if remove_tu:
        auto_actions.append(
            {
                "rule": "remove_garbage_segment",
                "message": f"Segment removed as garbage: {remove_reason}",
                "before_src": cleaned_src,
                "after_src": "",
                "before_tgt": cleaned_tgt,
                "after_tgt": "",
                "remove_reason": remove_reason,
            }
        )
    elif opts.emit_warnings:
        length_warn = _length_anomaly_warning(src_plain, tgt_plain)
        if length_warn is not None:
            warnings.append(length_warn)

        src_lang_warn = _language_mismatch_warning(src_plain, src_lang, "source")
        if src_lang_warn is not None:
            warnings.append(src_lang_warn)
        tgt_lang_warn = _language_mismatch_warning(tgt_plain, tgt_lang, "target")
        if tgt_lang_warn is not None:
            warnings.append(tgt_lang_warn)

        if _is_identical_cross_lang(src_plain, tgt_plain, src_lang, tgt_lang):
            warnings.append(
                {
                    "rule": "identical_source_target",
                    "severity": "WARN",
                    "message": "Source and target are identical for different language codes.",
                }
            )

    return CleanupResult(
        src_inner_xml=cleaned_src,
        tgt_inner_xml=cleaned_tgt,
        src_plain_text=src_plain,
        tgt_plain_text=tgt_plain,
        auto_actions=auto_actions,
        warnings=warnings,
        remove_tu=remove_tu,
        remove_reason=remove_reason,
    )


def _normalize_inner_xml(inner_xml: str) -> str:
    if not inner_xml:
        return ""
    parts = _TAG_RE.split(inner_xml)
    normalized_parts: list[str] = []
    text_part_indexes: list[int] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            normalized_parts.append(part)
        else:
            normalized_text = _normalize_text_part(part)
            normalized_parts.append(normalized_text)
            text_part_indexes.append(len(normalized_parts) - 1)

    if text_part_indexes:
        first_text_idx = text_part_indexes[0]
        last_text_idx = text_part_indexes[-1]
        normalized_parts[first_text_idx] = normalized_parts[first_text_idx].lstrip(" ")
        normalized_parts[last_text_idx] = normalized_parts[last_text_idx].rstrip(" ")

    return "".join(normalized_parts)


def _strip_inline_tags_inner_xml(inner_xml: str) -> str:
    """Drop inline XML tags from seg inner XML while preserving readable text boundaries."""
    if not inner_xml:
        return ""
    seg = build_seg_from_inner_xml(inner_xml)
    chunks = [chunk for chunk in seg.itertext() if chunk]
    if not chunks:
        return ""
    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = _merge_chunks_after_tag_drop(merged, chunk)
    # Return XML-safe inner text so downstream parsing never sees raw '&'/'<'/'>'.
    return html.escape(merged.strip(" "), quote=False)


def _merge_chunks_after_tag_drop(left: str, right: str) -> str:
    left_had_space = left.endswith(" ")
    right_had_space = right.startswith(" ")
    left_core = left.rstrip(" ")
    right_core = right.lstrip(" ")
    if not left_core:
        return right_core
    if not right_core:
        return left_core
    if left_had_space or right_had_space or _should_insert_tag_boundary_space(left_core, right_core):
        return f"{left_core} {right_core}"
    return f"{left_core}{right_core}"


def _should_insert_tag_boundary_space(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_char = left[-1]
    right_char = right[0]
    if left_char.isspace() or right_char.isspace():
        return False
    if left_char in _NO_SPACE_AFTER or right_char in _NO_SPACE_BEFORE:
        return False
    if (left_char in _FORCE_SPACE_AFTER or left_char == "\u2026") and right_char.isalnum():
        return True
    if left_char.isalpha() and right_char.isalpha() and left_char.islower() and right_char.isupper():
        return True
    return False


def _normalize_text_part(text: str) -> str:
    return _MULTI_SPACE_RE.sub(" ", text)


def _plain_text(inner_xml: str) -> str:
    seg = build_seg_from_inner_xml(inner_xml)
    return "".join(seg.itertext()).strip()


def _source_has_meaning(text: str) -> bool:
    return _text_has_letters(text) or _text_has_digits(text)


def _text_has_letters(text: str) -> bool:
    return any(char.isalpha() for char in text)


def _text_has_digits(text: str) -> bool:
    return any(char.isdigit() for char in text)


def _is_numeric_only(text: str) -> bool:
    alnum_chars = [char for char in text if char.isalnum()]
    if not alnum_chars:
        return False
    if any(char.isalpha() for char in alnum_chars):
        return False
    return all(char.isdigit() for char in alnum_chars)


def _is_punctuation_or_empty(text: str) -> bool:
    compact = "".join(char for char in text if not char.isspace())
    if not compact:
        return True
    if _text_has_letters(compact) or _text_has_digits(compact):
        return False
    for char in compact:
        category = unicodedata.category(char)
        if not category.startswith("P") and not category.startswith("S"):
            return False
    return True


def _length_anomaly_warning(src_text: str, tgt_text: str) -> dict[str, Any] | None:
    src_len = sum(1 for char in src_text if char.isalpha() or char.isdigit())
    tgt_len = sum(1 for char in tgt_text if char.isalpha() or char.isdigit())
    if src_len < 6 or tgt_len < 2:
        return None
    ratio = tgt_len / src_len if src_len > 0 else 1.0
    if ratio < 0.35 or ratio > 2.8:
        return {
            "rule": "length_anomaly",
            "severity": "WARN",
            "message": f"Length ratio looks suspicious: {ratio:.2f} (target/source).",
            "ratio": ratio,
            "src_len": src_len,
            "tgt_len": tgt_len,
        }
    return None


def _language_mismatch_warning(text: str, lang_code: str, side: str) -> dict[str, Any] | None:
    primary_lang = (lang_code or "").split("-", 1)[0].lower()
    letters_count = sum(1 for char in text if char.isalpha())
    if letters_count < 3 or not primary_lang:
        return None

    latin_count = len(_LATIN_RE.findall(text))
    cyrillic_count = len(_CYRILLIC_RE.findall(text))
    cjk_count = len(_CJK_RE.findall(text))

    mismatch = False
    if primary_lang in _CYRILLIC_LANGS:
        mismatch = cyrillic_count == 0 and latin_count >= 3
    elif primary_lang in _CJK_LANGS:
        mismatch = cjk_count == 0 and (latin_count + cyrillic_count) >= 3
    else:
        mismatch = latin_count == 0 and cyrillic_count >= 3

    if not mismatch:
        return None
    return {
        "rule": f"lang_mismatch_{side}",
        "severity": "WARN",
        "message": f"Text script looks inconsistent with xml:lang={lang_code}.",
        "xml_lang": lang_code,
        "latin": latin_count,
        "cyrillic": cyrillic_count,
        "cjk": cjk_count,
    }


def _is_identical_cross_lang(src_text: str, tgt_text: str, src_lang: str, tgt_lang: str) -> bool:
    src_primary = (src_lang or "").split("-", 1)[0].lower()
    tgt_primary = (tgt_lang or "").split("-", 1)[0].lower()
    if not src_primary or not tgt_primary or src_primary == tgt_primary:
        return False

    src_norm = " ".join(src_text.split()).casefold()
    tgt_norm = " ".join(tgt_text.split()).casefold()
    if not src_norm or not tgt_norm:
        return False
    if not any(char.isalpha() for char in src_norm):
        return False
    return src_norm == tgt_norm
