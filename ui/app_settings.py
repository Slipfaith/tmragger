"""Shared QSettings factory for the GUI.

Centralizes the persistent-settings location so every part of the app writes to
the same INI file under %APPDATA% (Roaming) instead of the registry. This file
survives a PyInstaller one-file build (which extracts to a temp dir wiped on
exit) and needs no admin rights.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

from app_meta import APP_NAME

SETTINGS_ORG = APP_NAME
SETTINGS_APP = f"{APP_NAME}-gui"


def create_app_settings() -> QSettings:
    """Return a QSettings bound to the shared per-user INI file."""
    return QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORG,
        SETTINGS_APP,
    )
