"""Shared application metadata for GUI title, versioning and packaging."""

from __future__ import annotations

from pathlib import Path

APP_NAME = "tmragger"
APP_VERSION = "18.04.026"
APP_DESCRIPTION = "tmragger - tool for preparing TMX files for RAG"
APP_USER_MODEL_ID = "tmragger.app"

PROJECT_ROOT = Path(__file__).resolve().parent
ASSET_DIR = PROJECT_ROOT / "asset"
APP_ICON_SVG_PATH = ASSET_DIR / "main-ico.svg"
APP_ICON_ICO_PATH = ASSET_DIR / "main-ico.ico"
