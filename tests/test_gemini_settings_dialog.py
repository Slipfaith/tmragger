from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.widgets.gemini_settings_dialog import GeminiSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_gemini_settings_dialog_model_is_read_only_and_api_key_round_trip(qapp):
    dialog = GeminiSettingsDialog(model="gemini-fixed-model", api_key="  secret  ")

    assert dialog.model_edit.isReadOnly() is True
    assert dialog.model_edit.text() == "gemini-fixed-model"
    assert dialog.api_key() == "secret"

    dialog.api_key_edit.setText(" new-key ")
    assert dialog.api_key() == "new-key"
