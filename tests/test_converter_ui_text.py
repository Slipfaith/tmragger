from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from tmx2csv_app.gui import ConvertTab, ExcelToTmxTab


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture()
def logger() -> logging.Logger:
    return logging.getLogger("tests.converter-ui-text")


def _header_texts(table) -> list[str]:
    return [
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    ]


def test_convert_tab_uses_russian_status_and_headers(qapp, logger, tmp_path):
    tab = ConvertTab(base_dir=Path(tmp_path), logger=logger)

    assert tab.progress_label.text() == "Ожидание"
    assert _header_texts(tab.table) == [
        "Файл",
        "Языки",
        "TU",
        "Статус",
        "Результаты",
    ]
    assert not hasattr(tab, "output_edit")
    assert not hasattr(tab, "browse_output_button")
    assert any("папку output" in label.text() for label in tab.findChildren(QLabel))


def test_excel_tab_uses_russian_status_headers_and_field_labels(qapp, logger, tmp_path):
    tab = ExcelToTmxTab(base_dir=Path(tmp_path), logger=logger)

    assert tab.progress_label.text() == "Ожидание"
    assert _header_texts(tab.table) == ["Файл", "Статус", "TU", "Результат"]
    visible_labels = {label.text() for label in tab.findChildren(QLabel)}
    assert "Source" in visible_labels
    assert "Target" in visible_labels
    assert "Источник" not in visible_labels
    assert "Перевод" not in visible_labels
    assert any("папку output" in label for label in visible_labels)
