from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.widgets.gemini_panel import GeminiPanel
from ui.widgets.reports_panel import ReportsPanel
from ui.widgets.stages_panel import StagesPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_stages_panel_defaults_and_value_extraction(qapp):
    panel = StagesPanel()

    values = panel.values()

    assert values.enable_split is True
    assert values.enable_cleanup_spaces is True
    assert values.enable_cleanup_service_markup is True
    assert values.enable_cleanup_garbage is True
    assert values.enable_cleanup_warnings is True

    panel.enable_split_checkbox.setChecked(False)
    panel.enable_cleanup_garbage_checkbox.setChecked(False)

    values = panel.values()
    assert values.enable_split is False
    assert values.enable_cleanup_garbage is False


def test_gemini_panel_defaults_and_verify_toggle(qapp):
    panel = GeminiPanel()

    values = panel.values()

    assert values.verify_with_gemini is False
    assert values.gemini_api_key == ""
    assert values.gemini_model == "gemini-3.1-flash-lite-preview"
    assert values.gemini_input_price_per_1m == "0.10"
    assert values.gemini_output_price_per_1m == "0.40"
    assert panel.gemini_api_key_edit.isEnabled() is False
    assert panel.gemini_model_edit.isEnabled() is False

    panel.verify_checkbox.setChecked(True)
    assert panel.gemini_api_key_edit.isEnabled() is True
    assert panel.gemini_model_edit.isEnabled() is True
    assert panel.gemini_input_price_edit.isEnabled() is True
    assert panel.gemini_output_price_edit.isEnabled() is True

    panel.gemini_api_key_edit.setText("secret")
    panel.gemini_model_edit.setText("gemini-test")
    panel.gemini_input_price_edit.setText("0.12")
    panel.gemini_output_price_edit.setText("0.34")

    values = panel.values()
    assert values.verify_with_gemini is True
    assert values.gemini_api_key == "secret"
    assert values.gemini_model == "gemini-test"
    assert values.gemini_input_price_per_1m == "0.12"
    assert values.gemini_output_price_per_1m == "0.34"


def test_reports_panel_defaults_and_value_extraction(qapp):
    panel = ReportsPanel()

    values = panel.values()

    assert values.log_file == "tmx-repair.log"
    assert values.report_dir == Path("tmx-reports")
    assert values.html_report_dir == Path("tmx-reports")
    assert values.xlsx_report_dir == Path("tmx-reports")

    panel.log_file_edit.setText(" repair.log ")
    panel.report_dir_edit.setText(" json-reports ")
    panel.html_report_edit.setText(" html-reports ")
    panel.xlsx_report_edit.setText(" xlsx-reports ")

    values = panel.values()
    assert values.log_file == "repair.log"
    assert values.report_dir == Path("json-reports")
    assert values.html_report_dir == Path("html-reports")
    assert values.xlsx_report_dir == Path("xlsx-reports")

    panel.set_report_dir_enabled(False)
    assert panel.report_dir_edit.isEnabled() is False
    panel.set_report_dir_enabled(True)
    assert panel.report_dir_edit.isEnabled() is True
