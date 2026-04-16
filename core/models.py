"""Data models for the TMX repair tool."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Confidence(str, Enum):
    HIGH = "HIGH"      # Rule-based split, src/tgt counts match
    MEDIUM = "MEDIUM"  # Gemini-assisted alignment


@dataclass
class TranslationUnit:
    """Represents a single <tu> element from a TMX file."""
    index: int
    attribs: dict[str, str]
    props: list[tuple[str, str]]           # list of (type, value)
    segments: dict[str, str]               # lang -> segment text
    tuv_attribs: dict[str, dict[str, str]] # lang -> tuv element attributes
    raw_element: Any = field(default=None, repr=False)  # lxml element


@dataclass
class SplitProposal:
    """A proposed split of one multi-sentence TU into multiple TUs."""
    tu_index: int
    original_unit: TranslationUnit
    src_lang: str
    tgt_lang: str
    src_parts: list[str]
    tgt_parts: list[str]
    confidence: Confidence
    gemini_used: bool = False
    accepted: bool = True


@dataclass
class TmxDocument:
    """Parsed TMX document."""
    path: Path
    src_lang: str
    tgt_lang: str
    units: list[TranslationUnit]
    raw_tree: Any = field(default=None, repr=False)  # lxml ElementTree
