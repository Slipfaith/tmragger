from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray
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
    assert hasattr(window, "nav_logs_button")
    assert hasattr(window, "page_stack")
    assert hasattr(window, "status_strip_label")
    assert window.page_stack.count() == 3
    assert window.status_strip_label.text()

    window.nav_prompt_button.click()
    assert window.page_stack.currentWidget() is window.prompt_tab

    window.nav_logs_button.click()
    assert window.page_stack.currentWidget() is window.logs_tab

    window.nav_repair_button.click()
    assert window.page_stack.currentWidget() is window.repair_tab

    window.close()


def test_main_window_persists_window_size_between_runs(qapp, monkeypatch):
    storage: dict[str, object] = {}

    class _MemorySettings:
        def value(self, key: str):
            return storage.get(key)

        def setValue(self, key: str, value: object) -> None:
            storage[key] = value

        def sync(self) -> None:
            return None

    monkeypatch.setattr(MainWindow, "_create_qsettings", lambda self: _MemorySettings())

    first = MainWindow()
    first.show()
    qapp.processEvents()
    first.resize(1180, 760)
    qapp.processEvents()
    first.close()

    geometry_key = MainWindow.SETTINGS_WINDOW_GEOMETRY_KEY
    state_key = MainWindow.SETTINGS_WINDOW_STATE_KEY
    assert geometry_key in storage
    assert state_key in storage
    assert isinstance(storage[geometry_key], QByteArray)
    assert isinstance(storage[state_key], QByteArray)

    restored: dict[str, QByteArray] = {}

    def _capture_restore_geometry(self, payload: QByteArray) -> bool:
        restored["geometry"] = payload
        return True

    def _capture_restore_state(self, payload: QByteArray) -> bool:
        restored["state"] = payload
        return True

    monkeypatch.setattr(MainWindow, "restoreGeometry", _capture_restore_geometry)
    monkeypatch.setattr(MainWindow, "restoreState", _capture_restore_state)

    second = MainWindow()
    assert restored["geometry"] == storage[geometry_key]
    assert restored["state"] == storage[state_key]
    second.close()
