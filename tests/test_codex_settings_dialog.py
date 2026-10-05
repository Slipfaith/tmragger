from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.widgets.codex_settings_dialog import CodexSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_codex_settings_dialog_defaults_to_luna_medium(qapp):
    dialog = CodexSettingsDialog(model="", reasoning_effort="")

    assert dialog.model() == "gpt-6-luna"
    assert dialog.reasoning_effort() == "medium"


def test_codex_settings_dialog_keeps_custom_model_and_effort(qapp):
    dialog = CodexSettingsDialog(model="gpt-6-astra", reasoning_effort="high", codex_bin="codex.exe")

    assert dialog.model() == "gpt-6-astra"
    assert dialog.reasoning_effort() == "high"
    assert "codex.exe" in dialog.status_label.text()
