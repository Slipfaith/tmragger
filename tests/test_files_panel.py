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


def test_set_input_paths_round_trip_dedupes(qapp):
    panel = FilesPanel()
    panel.set_input_paths([Path("alpha.tmx"), Path("alpha.tmx"), Path("beta.tmx")])

    assert panel.input_paths() == [Path("alpha.tmx"), Path("beta.tmx")]


def test_input_paths_normalize_file_uris_and_quotes(qapp):
    panel = FilesPanel()
    panel.input_edit.setPlainText(
        'file:///C:/Data/one.tmx\n"C:\\Data\\two.tmx"\nfile://srv/share/three.tmx\n'
    )

    assert panel.input_paths() == [
        Path(r"C:\Data\one.tmx"),
        Path(r"C:\Data\two.tmx"),
        Path(r"\\srv\share\three.tmx"),
    ]


def test_output_dir_round_trip_and_drop_signal_passthrough(qapp):
    panel = FilesPanel()
    panel.set_output_dir(Path("out"))
    assert panel.output_dir() == Path("out")

    received: list[list[str]] = []
    panel.files_dropped.connect(received.append)
    panel.drop_zone.files_dropped.emit(["sample.tmx"])

    assert received == [["sample.tmx"]]
