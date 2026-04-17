"""Tests for GUI view-state defaults and mapping."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.state import ViewState


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_view_state_defaults_match_repair_tab_defaults():
    state = ViewState.defaults()
    assert state.input_paths == []
    assert state.output_dir is None
    assert state.dry_run is False
    assert state.enable_split is True
    assert state.enable_cleanup_spaces is True
    assert state.enable_cleanup_service_markup is True
    assert state.enable_cleanup_garbage is True
    assert state.enable_cleanup_warnings is True
    assert state.verify_with_gemini is False
    assert state.gemini_api_key == ""
    assert state.log_file == "tmx-repair.log"
    assert state.report_dir == Path("tmx-reports")
    assert state.html_report_dir == Path("tmx-reports")
    assert state.xlsx_report_dir == Path("tmx-reports")


def test_view_state_round_trip_updates_widgets_and_back(qapp):
    window = MainWindow()
    expected = ViewState(
        input_paths=[Path("alpha.tmx"), Path("beta.tmx")],
        output_dir=Path("out"),
        dry_run=True,
        enable_split=False,
        enable_cleanup_spaces=False,
        enable_cleanup_service_markup=True,
        enable_cleanup_garbage=False,
        enable_cleanup_warnings=True,
        verify_with_gemini=True,
        gemini_api_key="secret-key",
        gemini_model="gemini-test-model",
        gemini_input_price_per_1m="1.23",
        gemini_output_price_per_1m="4.56",
        log_file="custom.log",
        report_dir=Path("reports/json"),
        html_report_dir=Path("reports/html"),
        xlsx_report_dir=Path("reports/xlsx"),
    )

    window._apply_view_state(expected)

    assert window.gemini_panel.verify_checkbox.isChecked() is True
    assert window.reports_panel.report_dir_edit.isEnabled() is True
    assert window._read_view_state() == expected

    window.close()
