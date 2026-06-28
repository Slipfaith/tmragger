from __future__ import annotations

import inspect
import logging
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from tmx2csv_app.excel_to_tmx import convert_excel_to_tmx
from tmx2csv_app.gui import ConversionWorker, ExcelToTmxWorker


FILES = [Path("first/one.tmx"), Path("second/two.tmx")]
LOGGER = logging.getLogger("tests.output-paths")


def test_conversion_worker_uses_each_inputs_sibling_output(monkeypatch) -> None:
    assert "output_dir" not in inspect.signature(ConversionWorker).parameters
    captured: list[Path] = []

    analysis = SimpleNamespace(tu_count=1)
    monkeypatch.setattr("tmx2csv_app.gui.analyze_tmx", lambda path: analysis)
    monkeypatch.setattr("tmx2csv_app.gui.build_pair_specs", lambda value: [])

    def fake_convert(value, output_dir, formats, progress_callback=None):
        captured.append(output_dir)
        return SimpleNamespace(output_files=[])

    monkeypatch.setattr("tmx2csv_app.gui.convert_tmx_file", fake_convert)
    worker = ConversionWorker(file_paths=FILES, formats=["csv"], logger=LOGGER)
    worker.run()

    assert captured == [Path("first/output"), Path("second/output")]


def test_excel_worker_passes_each_inputs_sibling_output(monkeypatch) -> None:
    captured: list[Path] = []

    def fake_convert_excel_to_tmx(**kwargs):
        captured.append(kwargs["output_dir"])
        return SimpleNamespace(output_file=Path("result.tmx"), rows_written=1)

    monkeypatch.setattr("tmx2csv_app.gui.convert_excel_to_tmx", fake_convert_excel_to_tmx)
    worker = ExcelToTmxWorker(
        file_paths=[Path("first/one.xlsx"), Path("second/two.xlsx")],
        source_lang="en",
        target_lang="ru",
        has_header=True,
        source_column=1,
        target_column=2,
        comment_column=3,
        logger=LOGGER,
    )
    worker.run()

    assert captured == [Path("first/output"), Path("second/output")]


def test_excel_conversion_writes_to_sibling_output(tmp_path) -> None:
    input_path = tmp_path / "source.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["source", "target", "comment"])
    sheet.append(["Hello", "Privet", ""])
    workbook.save(input_path)
    workbook.close()

    result = convert_excel_to_tmx(
        input_path=input_path,
        source_lang="en",
        target_lang="ru",
    )

    assert result.output_file == tmp_path / "output" / "source.tmx"
    assert result.output_file.exists()
    assert not input_path.with_suffix(".tmx").exists()
