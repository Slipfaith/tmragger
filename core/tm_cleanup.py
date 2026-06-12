"""TM cleanup rules for AUTO normalization/removal and WARN diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import re
from typing import Any, NamedTuple
import unicodedata

from core.splitter import build_seg_from_inner_xml


_TAG_RE = re.compile(r"(<[^>]+>)")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_GAME_MARKUP_RE = re.compile(r"\^\{[^{}\n]{1,80}\}\^")
_GAME_VARIANT_RE = re.compile(r"\$m\(([^|()]*)\|([^()]*)\)", re.IGNORECASE)
_ENCODED_COLOR_TAG_RE = re.compile(
    r"&(?:amp;)?lt;\s*/?\s*color(?:\s*=[^&<>]{0,120})?\s*&(?:amp;)?gt;",
    re.IGNORECASE,
)
_RAW_COLOR_TAG_RE = re.compile(
    r"<\s*/?\s*color(?:\s*=[^<>]{0,120})?\s*>",
    re.IGNORECASE,
)
_PSEUDO_TAG_RE = re.compile(
    r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s+[^<>]{0,200})?\s*/?>"
)
_PERCENT_WRAPPED_TOKEN_RE = re.compile(r"%(?:[A-Za-z_][A-Za-z0-9_.-]{0,80})%+")
_LINK_SCHEME_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
_LINK_DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?$",
    re.IGNORECASE,
)
# Expected writing system per primary language code. Languages not listed
# fall back to Latin, matching the historical default. Used by the
# script-mismatch WARN diagnostic.
_EXPECTED_SCRIPT: dict[str, str] = {
    # Cyrillic
    "ru": "cyrillic", "uk": "cyrillic", "be": "cyrillic", "bg": "cyrillic",
    "mk": "cyrillic", "sr": "cyrillic", "kk": "cyrillic", "ky": "cyrillic",
    "tg": "cyrillic", "tt": "cyrillic", "mn": "cyrillic",
    # CJK
    "zh": "cjk", "ja": "cjk", "ko": "cjk",
    # Greek
    "el": "greek",
    # Arabic script (incl. Persian/Urdu/Pashto)
    "ar": "arabic", "fa": "arabic", "ur": "arabic", "ps": "arabic", "sd": "arabic",
    # Hebrew (incl. Yiddish)
    "he": "hebrew", "iw": "hebrew", "yi": "hebrew",
    # Thai
    "th": "thai",
    # Devanagari
    "hi": "devanagari", "mr": "devanagari", "ne": "devanagari", "sa": "devanagari",
}
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
    remove_percent_wrapped_tokens: bool = False
    remove_game_markup: bool = True
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

    if opts.remove_percent_wrapped_tokens:
        percent_cleaned_src = _remove_percent_wrapped_tokens_inner_xml(cleaned_src)
        percent_cleaned_tgt = _remove_percent_wrapped_tokens_inner_xml(cleaned_tgt)
        if percent_cleaned_src != cleaned_src or percent_cleaned_tgt != cleaned_tgt:
            auto_actions.append(
                {
                    "rule": "remove_percent_wrapped_tokens",
                    "message": "Removed conservative %token% placeholders with safe spacing normalization.",
                    "before_src": cleaned_src,
                    "after_src": percent_cleaned_src,
                    "before_tgt": cleaned_tgt,
                    "after_tgt": percent_cleaned_tgt,
                }
            )
            cleaned_src = percent_cleaned_src
            cleaned_tgt = percent_cleaned_tgt

    if opts.remove_game_markup:
        markup_cleaned_src = _strip_game_markup_inner_xml(cleaned_src)
        markup_cleaned_tgt = _strip_game_markup_inner_xml(cleaned_tgt)
        if markup_cleaned_src != cleaned_src or markup_cleaned_tgt != cleaned_tgt:
            auto_actions.append(
                {
                    "rule": "remove_game_markup",
                    "message": (
                        "Game markup removed (^{...}^, $m(...|...), "
                        "&lt;Color=...&gt;...&lt;/Color&gt;) with safe spacing normalization."
                    ),
                    "before_src": cleaned_src,
                    "after_src": markup_cleaned_src,
                    "before_tgt": cleaned_tgt,
                    "after_tgt": markup_cleaned_tgt,
                }
            )
            cleaned_src = markup_cleaned_src
            cleaned_tgt = markup_cleaned_tgt

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
        if _is_effectively_empty(src_plain) or _is_effectively_empty(tgt_plain):
            remove_tu = True
            remove_reason = "empty_source_or_target"
        elif _is_numeric_only(src_plain) or _is_numeric_only(tgt_plain):
            remove_tu = True
            remove_reason = "numeric_only_side"
        elif _is_link_only(src_plain) or _is_link_only(tgt_plain):
            remove_tu = True
            remove_reason = "link_only_side"

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
        # One character scan per side feeds both the length-ratio and the
        # script-mismatch checks instead of re-scanning the text repeatedly.
        src_scan = _scan_scripts(src_plain)
        tgt_scan = _scan_scripts(tgt_plain)

        length_warn = _length_anomaly_warning(src_scan.alnum, tgt_scan.alnum)
        if length_warn is not None:
            warnings.append(length_warn)

        src_lang_warn = _language_mismatch_warning(src_scan, src_lang, "source")
        if src_lang_warn is not None:
            warnings.append(src_lang_warn)
        tgt_lang_warn = _language_mismatch_warning(tgt_scan, tgt_lang, "target")
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


def clean_service_markup_text(
    text: str,
    *,
    remove_percent_wrapped_tokens: bool,
    remove_game_markup: bool,
) -> tuple[str, list[str]]:
    """Clean service markup in arbitrary TM text (for example x-ContextContent).

    This helper intentionally uses only text-safe stages that do not require a
    valid XML fragment. It returns the cleaned text and the list of applied
    rule names (subset of ``remove_percent_wrapped_tokens`` /
    ``remove_game_markup``).
    """
    cleaned = text or ""
    applied_rules: list[str] = []

    if remove_percent_wrapped_tokens:
        next_text = _remove_percent_wrapped_tokens_inner_xml(cleaned)
        if next_text != cleaned:
            applied_rules.append("remove_percent_wrapped_tokens")
            cleaned = next_text

    if remove_game_markup:
        next_text = _strip_game_markup_inner_xml(cleaned)
        without_raw_color = _RAW_COLOR_TAG_RE.sub(" ", next_text)
        if without_raw_color != next_text:
            next_text = _normalize_inner_xml(without_raw_color)
        if next_text != cleaned:
            applied_rules.append("remove_game_markup")
            cleaned = next_text

    return cleaned, applied_rules


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
    merged = _strip_decoded_pseudo_tags(merged)
    # Return XML-safe inner text so downstream parsing never sees raw '&'/'<'/'>'.
    return html.escape(merged.strip(" "), quote=False)


def _strip_decoded_pseudo_tags(text: str) -> str:
    if not text:
        return ""
    cleaned = _PSEUDO_TAG_RE.sub(" ", text)
    if cleaned == text:
        return text
    return _normalize_text_part(cleaned)


def _strip_game_markup_inner_xml(inner_xml: str) -> str:
    """Drop non-XML game markup tokens from text parts while preserving XML tags."""
    if not inner_xml:
        return ""
    parts = _TAG_RE.split(inner_xml)
    cleaned_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            cleaned_parts.append(part)
            continue
        cleaned = _strip_game_markup_text_part(part)
        if cleaned != part:
            changed = True
        cleaned_parts.append(cleaned)

    merged = "".join(cleaned_parts)
    if changed:
        # Keep whitespace policy consistent with normalize_spaces:
        # collapse only ASCII doubled spaces and trim segment edges.
        merged = _normalize_inner_xml(merged)
    return merged


def _strip_game_markup_text_part(text: str) -> str:
    without_braced = _GAME_MARKUP_RE.sub(" ", text)
    without_encoded_color = _ENCODED_COLOR_TAG_RE.sub(" ", without_braced)
    return _GAME_VARIANT_RE.sub(lambda m: m.group(1), without_encoded_color)


def _remove_percent_wrapped_tokens_inner_xml(inner_xml: str) -> str:
    if not inner_xml:
        return ""
    parts = _TAG_RE.split(inner_xml)
    cleaned_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            cleaned_parts.append(part)
            continue
        cleaned = _PERCENT_WRAPPED_TOKEN_RE.sub(" ", part)
        if cleaned != part:
            changed = True
        cleaned_parts.append(cleaned)
    merged = "".join(cleaned_parts)
    if changed:
        merged = _normalize_inner_xml(merged)
    return merged


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
    # Single pass with early exit: any alphanumeric that is not a digit
    # (a letter, or a non-digit numeral like ½) means "not numeric only".
    has_digit = False
    for char in text:
        if char.isalnum():
            if not char.isdigit():
                return False
            has_digit = True
    return has_digit


def _is_punctuation_or_empty(text: str) -> bool:
    # Single pass with early exit. The common case (text starts with a
    # letter/digit) returns after the first non-space char instead of
    # building an intermediate string and scanning it several times.
    for char in text:
        if char.isspace():
            continue
        if char.isalpha() or char.isdigit():
            return False
        category = unicodedata.category(char)
        if category[0] != "P" and category[0] != "S":
            return False
    return True


def _is_effectively_empty(text: str) -> bool:
    return _is_punctuation_or_empty(text)


def _is_link_only(text: str) -> bool:
    compact = " ".join((text or "").split()).strip()
    if not compact:
        return False
    tokens = compact.split(" ")
    if not tokens:
        return False
    for token in tokens:
        normalized = token.strip(".,;:!?()[]{}<>\"'")
        if not normalized:
            return False
        if _LINK_SCHEME_RE.match(normalized):
            continue
        if _LINK_DOMAIN_RE.match(normalized):
            continue
        return False
    return True


class _ScriptStats(NamedTuple):
    """Per-side character tallies computed in a single pass over plain text."""
    alpha: int
    digit: int
    latin: int
    cyrillic: int
    cjk: int
    arabic: int
    greek: int
    hebrew: int
    thai: int
    devanagari: int

    @property
    def alnum(self) -> int:
        return self.alpha + self.digit

    def script_count(self, name: str) -> int:
        return getattr(self, name)

    @property
    def tracked_letters(self) -> int:
        return (
            self.latin + self.cyrillic + self.cjk + self.arabic
            + self.greek + self.hebrew + self.thai + self.devanagari
        )


def _scan_scripts(text: str) -> _ScriptStats:
    # Single pass replacing several per-character sums and regex scans
    # (length-ratio counting plus Latin/Cyrillic/CJK script counting). Letters
    # are bucketed by Unicode code-point range; "latin" stays ASCII-only to
    # match the historical [A-Za-z] behavior.
    alpha = digit = latin = cyrillic = cjk = 0
    arabic = greek = hebrew = thai = devanagari = 0
    for char in text:
        if char.isdigit():
            digit += 1
            continue
        if not char.isalpha():
            continue
        alpha += 1
        code = ord(char)
        if 0x41 <= code <= 0x5A or 0x61 <= code <= 0x7A:
            latin += 1
        elif 0x400 <= code <= 0x4FF:
            cyrillic += 1
        elif (
            0x3040 <= code <= 0x30FF
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xAC00 <= code <= 0xD7AF
        ):
            cjk += 1
        elif 0x600 <= code <= 0x6FF or 0x750 <= code <= 0x77F:
            arabic += 1
        elif 0x370 <= code <= 0x3FF:
            greek += 1
        elif 0x590 <= code <= 0x5FF:
            hebrew += 1
        elif 0x0E00 <= code <= 0x0E7F:
            thai += 1
        elif 0x900 <= code <= 0x97F:
            devanagari += 1
    return _ScriptStats(
        alpha, digit, latin, cyrillic, cjk, arabic, greek, hebrew, thai, devanagari
    )


def _length_anomaly_warning(src_len: int, tgt_len: int) -> dict[str, Any] | None:
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


def _language_mismatch_warning(
    scan: _ScriptStats, lang_code: str, side: str
) -> dict[str, Any] | None:
    primary_lang = (lang_code or "").split("-", 1)[0].lower()
    if scan.alpha < 3 or not primary_lang:
        return None

    expected_script = _EXPECTED_SCRIPT.get(primary_lang, "latin")
    expected_count = scan.script_count(expected_script)
    # Foreign = letters in any tracked script other than the expected one.
    # When none of the expected script is present but a sizable amount of a
    # different script is, the segment's language is almost certainly wrong.
    foreign_count = scan.tracked_letters - expected_count

    if expected_count != 0 or foreign_count < 3:
        return None

    return {
        "rule": f"lang_mismatch_{side}",
        "severity": "WARN",
        "message": f"Text script looks inconsistent with xml:lang={lang_code}.",
        "xml_lang": lang_code,
        "expected_script": expected_script,
        "latin": scan.latin,
        "cyrillic": scan.cyrillic,
        "cjk": scan.cjk,
        "arabic": scan.arabic,
        "greek": scan.greek,
        "hebrew": scan.hebrew,
        "thai": scan.thai,
        "devanagari": scan.devanagari,
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
