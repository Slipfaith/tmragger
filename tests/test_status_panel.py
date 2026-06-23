"""Tests for the extracted GUI status panel."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase, QFontMetrics
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
    assert "текущая~3.2" in panel.rate_text()
    assert "first line" in panel.log_text()


def test_status_panel_uses_russian_labels_and_stable_numeric_font(qapp):
    panel = StatusPanel()
    panel.set_status("выполняется")
    panel.set_progress("файл 1/3")
    panel.set_elapsed("00:07")

    assert panel.status_text().startswith("Статус:")
    assert panel.progress_text().startswith("Прогресс:")
    assert panel.elapsed_text().startswith("Время:")
    fixed_family = QFontDatabase.systemFont(
        QFontDatabase.SystemFont.FixedFont
    ).family()
    for label in (panel.usage_label, panel.rate_label, panel.elapsed_label):
        assert label.font().family() == fixed_family
        metrics = QFontMetrics(label.font())
        digit_widths = {metrics.horizontalAdvance(str(digit)) for digit in range(10)}
        assert len(digit_widths) == 1


def test_status_panel_caps_log_history(qapp):
    panel = StatusPanel()
    for index in range(panel.MAX_LOG_LINES + 25):
        panel.append_log(f"line {index}")

    log_text = panel.log_text()
    assert "line 0" not in log_text
    assert f"line {panel.MAX_LOG_LINES + 24}" in log_text
    assert len(log_text.splitlines()) <= panel.MAX_LOG_LINES

