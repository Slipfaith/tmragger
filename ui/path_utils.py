"""Path normalization helpers for GUI input and drag-and-drop."""

from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import unquote, urlparse


_WINDOWS_DRIVE_URI_RE = re.compile(r"^[\\/][A-Za-z]:")


def normalize_input_path(raw: str) -> str:
    """Normalize user-provided path text, including file:// URIs and UNC paths."""
    text = (raw or "").strip().strip('"')
    if not text:
        return ""
    if not text.lower().startswith("file:"):
        return text

    parsed = urlparse(text)
    if parsed.scheme.lower() != "file":
        return text

    netloc = unquote(parsed.netloc or "")
    path_part = unquote(parsed.path or "")
    if netloc:
        # file://server/share/folder/file.tmx -> \\server\share\folder\file.tmx
        tail = path_part.replace("/", "\\").lstrip("\\")
        return f"\\\\{netloc}\\{tail}"

    normalized = path_part.replace("/", "\\")
    # file:///C:/dir/file.tmx -> C:\dir\file.tmx
    if _WINDOWS_DRIVE_URI_RE.match(normalized):
        normalized = normalized[1:]
    return normalized


def normalize_path_obj(raw: str) -> Path:
    """Normalize path text and convert to Path."""
    return Path(normalize_input_path(raw))
