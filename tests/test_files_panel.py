from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.widgets.files_panel import FilesPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_set_input_paths_round_trip_dedupes_and_shows_names_only(qapp):
    panel = FilesPanel()
    panel.set_input_paths([Path("C:/a/alpha.tmx"), Path("C:/a/alpha.tmx"), Path("D:/b/beta.tmx")])

    assert panel.input_paths() == [Path("C:/a/alpha.tmx"), Path("D:/b/beta.tmx")]
    assert panel.input_list.count() == 2
    assert panel.input_list.item(0).text() == "1. alpha.tmx"
    assert panel.input_list.item(1).text() == "2. beta.tmx"
    assert panel.counter_label.text() == "Загружено: 2"


def test_drop_paths_normalize_and_emit_passthrough(qapp):
    panel = FilesPanel()
    received: list[list[str]] = []
    panel.files_dropped.connect(received.append)

    panel.drop_zone.files_dropped.emit(
        ['file:///C:/Data/one.tmx', '"C:\\Data\\two.tmx"', "file://srv/share/three.tmx"]
    )

    assert panel.input_paths() == [
        Path(r"C:\Data\one.tmx"),
        Path(r"C:\Data\two.tmx"),
        Path(r"\\srv\share\three.tmx"),
    ]
    assert panel.counter_label.text() == "Загружено: 3"
    assert received == [['file:///C:/Data/one.tmx', '"C:\\Data\\two.tmx"', "file://srv/share/three.tmx"]]


def test_remove_selected_and_counter_update(qapp):
    panel = FilesPanel()
    panel.set_input_paths([Path("a.tmx"), Path("b.tmx"), Path("c.tmx")])

    panel.input_list.item(0).setSelected(True)
    panel.input_list.item(2).setSelected(True)
    panel._remove_selected_inputs()

    assert panel.input_paths() == [Path("b.tmx")]
    assert panel.input_list.count() == 1
    assert panel.input_list.item(0).text() == "1. b.tmx"
    assert panel.counter_label.text() == "Загружено: 1"


def test_output_dir_round_trip(qapp):
    panel = FilesPanel()
    panel.set_output_dir(Path("out"))
    assert panel.output_dir() == Path("out")
