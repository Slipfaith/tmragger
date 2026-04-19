"""Sentence splitting helpers for TMX <seg> fragments."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import html
import re
from typing import Iterable
import xml.etree.ElementTree as ET


_SENTENCE_GAP_RE = re.compile(
    r'(?<=[.!?\u2026])(?:["\'\u201d\u00bb)\]]*)(?:\s+|(?=[A-Z\u0410-\u042f\u0401]))'
)
_PARAGRAPH_GAP_RE = re.compile(r"(?:\r?\n){2,}")
_QA_LINE_GAP_RE = re.compile(r"(?:\r?\n)(?=\s*(?:Q|A|\u0412|\u041e)\s*:)")
_WORD_RE = re.compile(r"\w")
_WORD_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_SHORT_SPLIT_MAX_WORDS = 3
_SHORT_SPLIT_MAX_CJK_CHARS = 12
_ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "sr.",
    "jr.",
    "st.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
    "f.a.q.",
    "\u0433.",
    "\u0443\u043b.",
}


@dataclass(frozen=True)
class SegmentToken:
    is_tag: bool
    value: str


def build_seg_from_inner_xml(inner_xml: str) -> ET.Element:
    """Create a <seg> element from raw inner XML text."""
    if not inner_xml:
        return ET.Element("seg")

    wrapped = f"<root><seg>{inner_xml}</seg></root>"
    root = ET.fromstring(wrapped)
    seg = root.find("seg")
    if seg is None:
        return ET.Element("seg")
    return seg


def seg_to_inner_xml(seg: ET.Element | None) -> str:
    """Serialize inner XML of a <seg> element."""
    if seg is None:
        return ""

    parts: list[str] = []
    if seg.text:
        parts.append(html.escape(seg.text, quote=False))

    for child in list(seg):
        child_copy = deepcopy(child)
        child_copy.tail = None
        parts.append(
            ET.tostring(
                child_copy,
                encoding="unicode",
                method="xml",
                short_empty_elements=True,
            )
        )
        if child.tail:
            parts.append(html.escape(child.tail, quote=False))

    return "".join(parts)


def split_inner_xml_into_sentences(inner_xml: str) -> list[str]:
    """Split a seg inner XML string into sentence-aligned parts."""
    normalized = inner_xml.strip()
    if not normalized:
        return []

    seg = build_seg_from_inner_xml(normalized)
    tokens = _seg_to_tokens(seg)
    plain_text = _tokens_plain_text(tokens)
    boundaries = _sentence_boundaries(plain_text)
    if not boundaries:
        return [normalized]

    chunks = _split_tokens(tokens, boundaries)
    parts = [_tokens_to_inner_xml(chunk) for chunk in chunks if _tokens_plain_text(chunk).strip()]
    if len(parts) < 2:
        return [normalized]
    return parts


def propose_aligned_split(
    src_inner_xml: str,
    tgt_inner_xml: str,
    *,
    enable_short_sentence_pair_guard: bool = True,
) -> tuple[list[str], list[str]] | None:
    """Propose split only if source/target split counts are aligned."""
    src_parts = split_inner_xml_into_sentences(src_inner_xml)
    tgt_parts = split_inner_xml_into_sentences(tgt_inner_xml)
    if len(src_parts) <= 1 or len(tgt_parts) <= 1:
        return None

    if len(src_parts) != len(tgt_parts):
        reconciled = _reconcile_split_counts(src_parts, tgt_parts)
        if reconciled is None:
            return None
        src_parts, tgt_parts = reconciled

    if any(not _plain_text_from_inner_xml(part).strip() for part in src_parts):
        return None
    if any(not _plain_text_from_inner_xml(part).strip() for part in tgt_parts):
        return None
    # Do not allow split outputs that produce standalone numeric-only segments
    # like "1." or "2024" as separate TU parts.
    if any(
        _is_numeric_only_sentence_piece(_plain_text_from_inner_xml(part))
        for part in (src_parts + tgt_parts)
    ):
        return None

    # Guard against over-splitting tiny two-part pairs like "Hello. Thanks."
    # where each side typically reads better as a single TM unit.
    if enable_short_sentence_pair_guard and _is_short_two_part_pair(src_parts, tgt_parts):
        return None

    return src_parts, tgt_parts


def _is_short_two_part_pair(src_parts: list[str], tgt_parts: list[str]) -> bool:
    if len(src_parts) != 2 or len(tgt_parts) != 2:
        return False
    all_parts = src_parts + tgt_parts
    return all(_is_short_sentence_piece(_plain_text_from_inner_xml(part)) for part in all_parts)


def _is_short_sentence_piece(text: str) -> bool:
    plain = text.strip()
    if not plain:
        return False
    if _CJK_RE.search(plain):
        compact = re.sub(r"\s+", "", plain)
        return len(compact) <= _SHORT_SPLIT_MAX_CJK_CHARS
    return len(_WORD_TOKEN_RE.findall(plain)) <= _SHORT_SPLIT_MAX_WORDS


def _is_numeric_only_sentence_piece(text: str) -> bool:
    compact_alnum = [char for char in text if char.isalnum()]
    if not compact_alnum:
        return False
    if any(char.isalpha() for char in compact_alnum):
        return False
    return all(char.isdigit() for char in compact_alnum)


def _reconcile_split_counts(
    src_parts: list[str],
    tgt_parts: list[str],
) -> tuple[list[str], list[str]] | None:
    src_count = len(src_parts)
    tgt_count = len(tgt_parts)
    if src_count == tgt_count:
        return src_parts, tgt_parts
    if min(src_count, tgt_count) < 2:
        return None

    shorter_count = min(src_count, tgt_count)
    longer_count = max(src_count, tgt_count)
    extra = longer_count - shorter_count
    if extra <= 0:
        return src_parts, tgt_parts
    # Keep this conservative: only reconcile relatively small drifts.
    if extra > 4 or longer_count > int(shorter_count * 1.5):
        return None

    src_is_longer = src_count > tgt_count
    longer_parts = src_parts if src_is_longer else tgt_parts
    shorter_parts = tgt_parts if src_is_longer else src_parts
    merged = _merge_longer_side_to_target_count(
        longer_parts=longer_parts,
        shorter_parts=shorter_parts,
    )
    if merged is None:
        return None
    if src_is_longer:
        return merged, tgt_parts
    return src_parts, merged


def _merge_longer_side_to_target_count(
    longer_parts: list[str],
    shorter_parts: list[str],
) -> list[str] | None:
    k = len(shorter_parts)
    m = len(longer_parts)
    if k < 2 or m <= k:
        return None

    long_lengths = [_effective_text_len(part) for part in longer_parts]
    short_lengths = [_effective_text_len(part) for part in shorter_parts]
    total_long = sum(long_lengths)
    total_short = sum(short_lengths)
    if total_long <= 0 or total_short <= 0:
        return None

    scale = total_long / total_short
    targets = [max(1.0, length * scale) for length in short_lengths]
    prefix: list[int] = [0]
    for value in long_lengths:
        prefix.append(prefix[-1] + value)

    inf = float("inf")
    dp: list[list[float]] = [[inf] * (m + 1) for _ in range(k + 1)]
    prev: list[list[int]] = [[-1] * (m + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0

    for i in range(1, k + 1):
        min_j = i
        max_j = m - (k - i)
        for j in range(min_j, max_j + 1):
            best_cost = inf
            best_p = -1
            min_p = i - 1
            max_p = j - 1
            for p in range(min_p, max_p + 1):
                prev_cost = dp[i - 1][p]
                if prev_cost >= inf:
                    continue
                group_len = prefix[j] - prefix[p]
                cost = prev_cost + (group_len - targets[i - 1]) ** 2
                if cost < best_cost:
                    best_cost = cost
                    best_p = p
            dp[i][j] = best_cost
            prev[i][j] = best_p

    if dp[k][m] >= inf:
        return None

    groups: list[tuple[int, int]] = []
    i = k
    j = m
    while i > 0:
        p = prev[i][j]
        if p < 0:
            return None
        groups.append((p, j))
        i -= 1
        j = p
    groups.reverse()

    merged: list[str] = []
    abs_error_sum = 0.0
    for idx, (start, end) in enumerate(groups):
        chunk_parts = longer_parts[start:end]
        if not chunk_parts:
            return None
        merged.append(_join_inner_xml_chunks(chunk_parts))
        group_len = prefix[end] - prefix[start]
        abs_error_sum += abs(group_len - targets[idx])

    normalized_error = abs_error_sum / max(1.0, float(total_long))
    if normalized_error > 0.45:
        return None
    return merged


def _effective_text_len(inner_xml: str) -> int:
    plain = _plain_text_from_inner_xml(inner_xml).strip()
    return max(1, len(plain))


def _join_inner_xml_chunks(parts: list[str]) -> str:
    if not parts:
        return ""
    merged = parts[0].strip()
    for piece in parts[1:]:
        token = piece.strip()
        if not token:
            continue
        if not merged:
            merged = token
            continue
        if merged.endswith(("(", "[", "{", "«")):
            merged += token
        elif token.startswith((".", ",", "!", "?", ":", ";", ")", "]", "}", "»")):
            merged += token
        else:
            merged += " " + token
    return merged


def _seg_to_tokens(seg: ET.Element) -> list[SegmentToken]:
    tokens: list[SegmentToken] = []
    if seg.text:
        tokens.append(SegmentToken(is_tag=False, value=seg.text))

    for child in list(seg):
        child_copy = deepcopy(child)
        child_copy.tail = None
        tokens.append(
            SegmentToken(
                is_tag=True,
                value=ET.tostring(
                    child_copy,
                    encoding="unicode",
                    method="xml",
                    short_empty_elements=True,
                ),
            )
        )
        if child.tail:
            tokens.append(SegmentToken(is_tag=False, value=child.tail))
    return tokens


def _tokens_plain_text(tokens: Iterable[SegmentToken]) -> str:
    return "".join(token.value for token in tokens if not token.is_tag)


def _plain_text_from_inner_xml(inner_xml: str) -> str:
    seg = build_seg_from_inner_xml(inner_xml)
    return "".join(seg.itertext())


def _sentence_boundaries(text: str) -> set[int]:
    boundaries: set[int] = set()

    def add_boundary(boundary: int) -> None:
        if boundary <= 0 or boundary >= len(text):
            return
        suffix = text[boundary:].strip()
        if not _WORD_RE.search(suffix):
            return
        boundaries.add(boundary)

    for match in _SENTENCE_GAP_RE.finditer(text):
        boundary = match.start()
        if boundary <= 0:
            continue

        prefix = text[:boundary].rstrip().lower()
        if prefix.endswith("...") or prefix.endswith("\u2026"):
            # Do not split on ellipsis continuation: "I... we try our best".
            continue
        if any(prefix.endswith(abbr) for abbr in _ABBREVIATIONS):
            continue
        # Skip boundaries inside letter-dot acronyms like F.A.Q., U.S.A., etc.
        # If the text right after the boundary is "X." (single uppercase + dot),
        # we are still mid-acronym and should not split here.
        if re.match(r"\s*[A-Z]\.", text[boundary:]):
            continue

        suffix = text[match.end() :].strip()
        if not _WORD_RE.search(suffix):
            continue
        add_boundary(boundary)

    # FAQ/guide-like long segments often use paragraphs and Q/A blocks that
    # do not always end with strong punctuation. Allow safe splits on empty
    # lines and explicit Q:/A: or В:/О: line markers.
    for match in _PARAGRAPH_GAP_RE.finditer(text):
        add_boundary(match.start())
    for match in _QA_LINE_GAP_RE.finditer(text):
        add_boundary(match.start())

    return boundaries


def _split_tokens(tokens: list[SegmentToken], boundaries: set[int]) -> list[list[SegmentToken]]:
    if not boundaries:
        return [tokens]

    boundary_positions = set(boundaries)
    segments: list[list[SegmentToken]] = []
    current: list[SegmentToken] = []
    text_buffer: list[str] = []
    plain_count = 0

    def flush_text() -> None:
        nonlocal text_buffer
        if text_buffer:
            current.append(SegmentToken(is_tag=False, value="".join(text_buffer)))
            text_buffer = []

    def push_segment() -> None:
        nonlocal current
        flush_text()
        trimmed = _trim_tokens(current)
        if _tokens_plain_text(trimmed).strip():
            segments.append(trimmed)
        current = []

    for token in tokens:
        if token.is_tag:
            flush_text()
            current.append(token)
            continue

        for char in token.value:
            text_buffer.append(char)
            plain_count += 1
            if plain_count in boundary_positions:
                push_segment()

    push_segment()
    return segments


def _trim_tokens(tokens: list[SegmentToken]) -> list[SegmentToken]:
    if not tokens:
        return []

    trimmed = [SegmentToken(t.is_tag, t.value) for t in tokens]

    for i, token in enumerate(trimmed):
        if token.is_tag:
            continue
        trimmed[i] = SegmentToken(is_tag=False, value=token.value.lstrip())
        break

    for i in range(len(trimmed) - 1, -1, -1):
        token = trimmed[i]
        if token.is_tag:
            continue
        trimmed[i] = SegmentToken(is_tag=False, value=token.value.rstrip())
        break

    return [token for token in trimmed if token.is_tag or token.value]


def _tokens_to_inner_xml(tokens: list[SegmentToken]) -> str:
    out: list[str] = []
    for token in tokens:
        if token.is_tag:
            out.append(token.value)
        else:
            out.append(html.escape(token.value, quote=False))
    return "".join(out)
