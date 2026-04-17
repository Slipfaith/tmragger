"""Tests for the extracted GUI status panel."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.widgets.status_panel import StatusPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_status_panel_updates_text(qapp):
    panel = StatusPanel()
    panel.set_status("running")
    panel.set_progress("file 1/3")
    panel.set_usage(10, 5, 15, 0.001)
    panel.set_rate(3.25, 2.5, 0.75)
    panel.append_log("first line")

    assert "running" in panel.status_text()
    assert "file 1/3" in panel.progress_text()
    assert "вход=10" in panel.usage_text()
    assert "now~3.2" in panel.rate_text()
    assert "first line" in panel.log_text()

