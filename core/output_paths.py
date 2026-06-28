"""Shared automatic output-path policy."""

from __future__ import annotations

from pathlib import Path


OUTPUT_DIRECTORY_NAME = "output"


def sibling_output_dir(input_path: Path) -> Path:
    """Return the automatic output directory beside an input file."""
    return input_path.parent / OUTPUT_DIRECTORY_NAME
