from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.drop_zone import DropZone


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_drop_zone_activates_from_keyboard(qapp):
    zone = DropZone()
    clicks: list[bool] = []
    zone.clicked.connect(lambda: clicks.append(True))
    zone.show()
    zone.setFocus()

    QTest.keyClick(zone, Qt.Key.Key_Return)
    QTest.keyClick(zone, Qt.Key.Key_Space)

    assert clicks == [True, True]


def test_drop_zone_exposes_accessible_drag_state(qapp):
    zone = DropZone()

    assert zone.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert zone.accessibleName() == "Добавить TMX-файлы"
    assert zone.property("dragActive") is False

    zone._set_drag_active(True)
    assert zone.property("dragActive") is True
    zone._set_drag_active(False)
    assert zone.property("dragActive") is False
