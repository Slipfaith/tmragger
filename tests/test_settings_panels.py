from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.widgets.stages_panel import StagesPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_stages_panel_defaults_and_value_extraction(qapp):
    panel = StagesPanel()

    values = panel.values()

    assert values.enable_split is True
    assert values.enable_split_short_sentence_pair_guard is True
    assert values.verify_with_gemini is False
    assert values.enable_cleanup_spaces is True
    assert values.enable_cleanup_service_markup is True
    assert values.enable_cleanup_garbage is True
    assert values.enable_cleanup_warnings is True

    panel.enable_split_checkbox.setChecked(False)
    panel.enable_split_short_sentence_pair_guard_checkbox.setChecked(False)
    panel.enable_gemini_verification_checkbox.setChecked(True)
    panel.enable_cleanup_garbage_checkbox.setChecked(False)

    values = panel.values()
    assert values.enable_split is False
    assert values.enable_split_short_sentence_pair_guard is False
    assert values.verify_with_gemini is True
    assert values.enable_cleanup_garbage is False
