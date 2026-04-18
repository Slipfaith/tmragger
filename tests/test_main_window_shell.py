from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_main_window_builds_editorial_shell(qapp):
    window = MainWindow()

    assert window.centralWidget() is not None
    assert hasattr(window, "nav_repair_button")
    assert hasattr(window, "nav_prompt_button")
    assert hasattr(window, "page_stack")
    assert hasattr(window, "status_strip_label")
    assert window.page_stack.count() == 2
    assert window.status_strip_label.text()

    window.nav_prompt_button.click()
    assert window.page_stack.currentWidget() is window.prompt_tab

    window.nav_repair_button.click()
    assert window.page_stack.currentWidget() is window.repair_tab

    window.close()
