"""Environment loading helpers with safe fallback when python-dotenv is missing."""

from __future__ import annotations

import os
from pathlib import Path


def load_project_env() -> list[Path]:
    """Load .env from cwd/project root and return list of files that were applied."""
    candidates = _collect_env_candidates()
    loaded: list[Path] = []

    try:
        from dotenv import load_dotenv as dotenv_load  # type: ignore
    except Exception:
        dotenv_load = None

    for env_path in candidates:
        if not env_path.exists():
            continue
        loaded_from_dotenv = False
        if dotenv_load is not None:
            try:
                dotenv_load(dotenv_path=env_path, override=False, encoding="utf-8")
                loaded_from_dotenv = True
            except Exception:
                loaded_from_dotenv = False
        # Manual pass still runs even when python-dotenv is available:
        # it fills keys that are missing or present but empty.
        loaded_from_manual = _manual_load_env(env_path)
        if loaded_from_dotenv or loaded_from_manual:
            loaded.append(env_path)
    return loaded


def _collect_env_candidates() -> list[Path]:
    cwd_env = Path.cwd() / ".env"
    project_root_env = Path(__file__).resolve().parents[1] / ".env"
    ordered: list[Path] = []
    for path in (cwd_env, project_root_env):
        if path not in ordered:
            ordered.append(path)
    return ordered


def _manual_load_env(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        return False

    loaded_any = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        # Keep existing non-empty env vars, but allow filling empty placeholders.
        if key not in os.environ or not str(os.environ.get(key, "")).strip():
            os.environ[key] = value
        loaded_any = True
    return loaded_any
