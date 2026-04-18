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
    assert state.enable_split_short_sentence_pair_guard is True
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
        dry_run=False,
        enable_split=False,
        enable_split_short_sentence_pair_guard=False,
        enable_cleanup_spaces=False,
        enable_cleanup_service_markup=True,
        enable_cleanup_garbage=False,
        enable_cleanup_warnings=True,
        verify_with_gemini=True,
        gemini_api_key="secret-key",
        gemini_model=MainWindow.DEFAULT_GEMINI_MODEL,
        gemini_input_price_per_1m=f"{MainWindow.DEFAULT_GEMINI_INPUT_PRICE:.2f}",
        gemini_output_price_per_1m=f"{MainWindow.DEFAULT_GEMINI_OUTPUT_PRICE:.2f}",
        log_file=MainWindow.DEFAULT_LOG_FILE,
        report_dir=MainWindow.DEFAULT_REPORT_ROOT,
        html_report_dir=MainWindow.DEFAULT_REPORT_ROOT,
        xlsx_report_dir=MainWindow.DEFAULT_REPORT_ROOT,
    )

    window._apply_view_state(expected)

    assert window.stages_panel.enable_gemini_verification_checkbox.isChecked() is True
    assert window._read_view_state() == expected

    window.close()
