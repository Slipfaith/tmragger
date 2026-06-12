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


def test_gemini_settings_dialog_model_select_and_api_key_round_trip(qapp):
    dialog = GeminiSettingsDialog(
        model="gemini-3.1-flash-lite-preview",
        api_key="  secret  ",
        available_models=["gemini-3-pro-preview", "gemini-3.1-flash-lite-preview"],
    )

    assert dialog.model() == "gemini-3.1-flash-lite-preview"
    assert dialog.api_key() == "secret"

    dialog.api_key_edit.setText(" new-key ")
    assert dialog.api_key() == "new-key"


def test_gemini_settings_dialog_loads_models_via_loader(qapp):
    loaded: dict[str, str] = {}

    def _loader(api_key: str) -> list[str]:
        loaded["key"] = api_key
        return ["gemini-3-pro-preview", "gemini-3-flash"]

    dialog = GeminiSettingsDialog(
        model="gemini-3-pro-preview",
        api_key="my-key",
        available_models=["gemini-3-pro-preview"],
        models_loader=_loader,
    )
    assert dialog.load_models_button.isEnabled() is True

    dialog._on_load_models()

    assert loaded["key"] == "my-key"
    items = [dialog.model_combo.itemText(i) for i in range(dialog.model_combo.count())]
    assert items == ["gemini-3-pro-preview", "gemini-3-flash"]
    # Current selection is preserved across a reload.
    assert dialog.model() == "gemini-3-pro-preview"


def test_gemini_settings_dialog_load_button_disabled_without_loader(qapp):
    dialog = GeminiSettingsDialog(model="gemini-3-pro-preview", api_key="")
    assert dialog.load_models_button.isEnabled() is False
