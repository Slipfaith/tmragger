"""Text-diff primitives used by repair logs and HTML reports.

Kept deliberately small and HTML-free so core logic can pull in the
cheap helpers (``preview``) without dragging in the report module.
The HTML-producing helpers (``render_inline_diff``, ``visible_text``,
``whitespace_delta_summary``) live here too because they're a single
conceptual unit and have no Qt/openpyxl dependencies.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from html import escape


def preview(text: str, limit: int = 180) -> str:
    """Return a single-line, whitespace-collapsed, length-capped excerpt."""
    compact = " ".join(text.replace("\n", " ").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)]}..."


def visible_text(text: str, mark_spaces: bool) -> str:
    """HTML-escape ``text`` and optionally mark regular spaces with a ``·`` badge."""
    out: list[str] = []
    for char in text:
        if mark_spaces and char == " ":
            out.append('<span class="ws ws-space" title="SPACE">·</span>')
        else:
            out.append(escape(char))
    return "".join(out)


def whitespace_delta_summary(before: str, after: str) -> str:
    """One-line summary of how the space count changed between two strings."""
    before_counter = Counter(before)
    after_counter = Counter(after)
    before_spaces = before_counter.get(" ", 0)
    after_spaces = after_counter.get(" ", 0)
    delta = after_spaces - before_spaces
    if delta == 0:
        return "SPACE: unchanged"
    sign = "+" if delta > 0 else ""
    return f"SPACE: {before_spaces}->{after_spaces} ({sign}{delta})"


def render_inline_diff(before: str, after: str) -> str:
    """Return a two-line HTML diff block (``before:`` then ``after:``)."""
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    before_chunks: list[str] = []
    after_chunks: list[str] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        before_part = before[i1:i2]
        after_part = after[j1:j2]
        if op == "equal":
            if before_part:
                before_chunks.append(
                    f'<span class="diff-eq">{visible_text(before_part, mark_spaces=False)}</span>'
                )
            if after_part:
                after_chunks.append(
                    f'<span class="diff-eq">{visible_text(after_part, mark_spaces=False)}</span>'
                )
        elif op == "delete":
            if before_part:
                before_chunks.append(
                    f'<span class="diff-del">{visible_text(before_part, mark_spaces=True)}</span>'
                )
        elif op == "insert":
            if after_part:
                after_chunks.append(
                    f'<span class="diff-add">{visible_text(after_part, mark_spaces=True)}</span>'
                )
        elif op == "replace":
            if before_part:
                before_chunks.append(
                    f'<span class="diff-del">{visible_text(before_part, mark_spaces=True)}</span>'
                )
            if after_part:
                after_chunks.append(
                    f'<span class="diff-add">{visible_text(after_part, mark_spaces=True)}</span>'
                )

    before_html = "".join(before_chunks) or '<span class="diff-eq">(empty)</span>'
    after_html = "".join(after_chunks) or '<span class="diff-eq">(empty)</span>'
    return (
        '<div class="diff-wrap">'
        f'<div class="diff-line before-line"><span class="diff-label">Before:</span>{before_html}</div>'
        f'<div class="diff-line after-line"><span class="diff-label">After:</span>{after_html}</div>'
        '<div class="diff-note">Legend: changed regular spaces are marked as \u00b7</div>'
        f'<div class="diff-note">Whitespace delta: {whitespace_delta_summary(before, after)}</div>'
        '</div>'
    )
