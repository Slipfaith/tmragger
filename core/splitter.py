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
_WORD_RE = re.compile(r"\w")
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


def propose_aligned_split(src_inner_xml: str, tgt_inner_xml: str) -> tuple[list[str], list[str]] | None:
    """Propose split only if source/target split counts are aligned."""
    src_parts = split_inner_xml_into_sentences(src_inner_xml)
    tgt_parts = split_inner_xml_into_sentences(tgt_inner_xml)
    if len(src_parts) <= 1 or len(src_parts) != len(tgt_parts):
        return None

    if any(not _plain_text_from_inner_xml(part).strip() for part in src_parts):
        return None
    if any(not _plain_text_from_inner_xml(part).strip() for part in tgt_parts):
        return None
    return src_parts, tgt_parts


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

        suffix = text[match.end() :].strip()
        if not _WORD_RE.search(suffix):
            continue
        boundaries.add(boundary)
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
