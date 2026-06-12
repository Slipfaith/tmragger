from __future__ import annotations

import csv
from copy import deepcopy
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from openpyxl import Workbook

ProgressCallback = Callable[[int, int], None]


@dataclass(slots=True)
class TmxAnalysis:
    file_path: Path
    creationtool: str = ""
    creationtoolversion: str = ""
    srclang: str = ""
    datatype: str = ""
    tu_count: int = 0
    languages: list[str] = field(default_factory=list)
    prop_types: list[str] = field(default_factory=list)
    inline_tags: list[str] = field(default_factory=list)
    header_attrs: dict[str, str] = field(default_factory=dict)
    prefix_text: str = ""
    body_indent: str = ""


@dataclass(slots=True)
class ConversionResult:
    analysis: TmxAnalysis
    pair_columns: dict[str, list[str]]
    output_files: list[Path]


@dataclass(slots=True)
class PairSpec:
    source_lang: str
    target_lang: str

    @property
    def columns(self) -> list[str]:
        return [self.source_lang, self.target_lang]

    @property
    def suffix(self) -> str:
        return f"{_safe_name(self.source_lang)}-{_safe_name(self.target_lang)}"


def analyze_tmx(path: Path) -> TmxAnalysis:
    languages: set[str] = set()
    prop_types: set[str] = set()
    inline_tags: set[str] = set()
    header_attrs: dict[str, str] = {}
    tu_count = 0
    prefix_text = _read_prefix_text(path)
    body_indent = _extract_body_indent(prefix_text)

    context = ET.iterparse(path, events=("start", "end"))
    _, root = next(context)
    for event, elem in context:
        if event != "end":
            continue
        if elem.tag == "header":
            header_attrs = dict(elem.attrib)
        elif elem.tag == "tu":
            tu_count += 1
            for tuv in elem.findall("tuv"):
                lang = _get_lang(tuv)
                if lang:
                    languages.add(lang)
                for prop in tuv.findall("prop"):
                    prop_type = prop.attrib.get("type", "")
                    if prop_type:
                        prop_types.add(prop_type)
                seg = tuv.find("seg")
                if seg is not None:
                    for child in seg:
                        inline_tags.add(child.tag)
            root.clear()

    ordered_languages = sorted(languages, key=lambda lang: (lang != header_attrs.get("srclang", ""), lang))
    return TmxAnalysis(
        file_path=path,
        creationtool=header_attrs.get("creationtool", ""),
        creationtoolversion=header_attrs.get("creationtoolversion", ""),
        srclang=header_attrs.get("srclang", ""),
        datatype=header_attrs.get("datatype", ""),
        tu_count=tu_count,
        languages=ordered_languages,
        prop_types=sorted(prop_types),
        inline_tags=sorted(inline_tags),
        header_attrs=header_attrs,
        prefix_text=prefix_text,
        body_indent=body_indent,
    )


def build_pair_specs(analysis: TmxAnalysis) -> list[PairSpec]:
    source_lang = analysis.srclang or (analysis.languages[0] if analysis.languages else "")
    if not source_lang:
        raise ValueError(f"Could not detect source language for {analysis.file_path.name}.")

    target_langs = [lang for lang in analysis.languages if lang != source_lang]
    if not target_langs:
        raise ValueError(f"No target languages found in {analysis.file_path.name}.")

    return [PairSpec(source_lang=source_lang, target_lang=target_lang) for target_lang in target_langs]


def convert_tmx_file(
    analysis: TmxAnalysis,
    output_dir: Path,
    formats: Iterable[str],
    progress_callback: ProgressCallback | None = None,
) -> ConversionResult:
    requested_formats = {item.lower() for item in formats}
    if not requested_formats:
        raise ValueError("At least one output format must be selected.")

    pair_specs = build_pair_specs(analysis)
    output_dir.mkdir(parents=True, exist_ok=True)

    writer_sets = [_WriterSet(pair=pair) for pair in pair_specs]
    output_files: list[Path] = []
    pair_columns: dict[str, list[str]] = {}

    try:
        context = ET.iterparse(analysis.file_path, events=("start", "end"))
        _, root = next(context)
        tu_index = 0
        for event, elem in context:
            if event != "end" or elem.tag != "tu":
                continue
            tu_index += 1
            segments = _segments_from_tu(elem)
            for writer_set in writer_sets:
                source_text = segments.get(writer_set.pair.source_lang, "")
                target_text = segments.get(writer_set.pair.target_lang, "")
                if not _has_text(source_text) or not _has_text(target_text):
                    continue
                if not writer_set.writers and writer_set.tmx_writer is None:
                    created_files = _create_writers(writer_set, analysis, analysis.file_path.stem, output_dir, requested_formats)
                    output_files.extend(created_files)
                    pair_columns[writer_set.pair.target_lang] = writer_set.pair.columns
                row = {
                    writer_set.pair.source_lang: source_text,
                    writer_set.pair.target_lang: target_text,
                }
                for writer in writer_set.writers:
                    writer.write_row(row)
                if writer_set.tmx_writer is not None:
                    writer_set.tmx_writer.write_tu(elem, writer_set.pair.source_lang, writer_set.pair.target_lang)
                writer_set.rows_written += 1
            if progress_callback and (tu_index == analysis.tu_count or tu_index % 250 == 0):
                progress_callback(tu_index, analysis.tu_count)
            root.clear()
    except Exception:
        for writer_set in writer_sets:
            for writer in writer_set.writers:
                writer.abort()
            if writer_set.tmx_writer is not None:
                writer_set.tmx_writer.abort()
        for output_file in output_files:
            output_file.unlink(missing_ok=True)
        raise
    else:
        for writer_set in writer_sets:
            for writer in writer_set.writers:
                writer.close()
            if writer_set.tmx_writer is not None:
                writer_set.tmx_writer.close()

    if progress_callback:
        progress_callback(analysis.tu_count, analysis.tu_count)
    return ConversionResult(analysis=analysis, pair_columns=pair_columns, output_files=output_files)


def _segments_from_tu(tu: ET.Element) -> dict[str, str]:
    segments: dict[str, str] = {}
    for tuv in tu.findall("tuv"):
        lang = _get_lang(tuv)
        if not lang or lang in segments:
            continue
        seg = tuv.find("seg")
        segments[lang] = _segment_to_text(seg) if seg is not None else ""
    return segments


def _get_lang(tuv: ET.Element) -> str:
    for key, value in tuv.attrib.items():
        if key.endswith("lang"):
            return value
    return ""


def _segment_to_text(seg: ET.Element) -> str:
    return _node_to_text(seg).replace("\r\n", "\n")


def _node_to_text(node: ET.Element) -> str:
    parts: list[str] = [node.text or ""]
    for child in node:
        parts.append(_tag_token(child))
        parts.append(_node_to_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _tag_token(node: ET.Element) -> str:
    attrs = ", ".join(f"{key}={value}" for key, value in node.attrib.items())
    if attrs:
        return f"{{{node.tag}: {attrs}}}"
    return f"{{{node.tag}}}"


def _has_text(value: str) -> bool:
    return bool(value and value.strip())


def _safe_name(value: str) -> str:
    invalid_chars = '<>:"/\\|?*'
    return "".join("_" if char in invalid_chars else char for char in value)


@dataclass(slots=True)
class _WriterSet:
    pair: PairSpec
    writers: list["_RowWriter"] = field(default_factory=list)
    tmx_writer: "_TmxWriter | None" = None
    rows_written: int = 0


def _create_writers(
    writer_set: _WriterSet,
    analysis: TmxAnalysis,
    stem: str,
    output_dir: Path,
    requested_formats: set[str],
) -> list[Path]:
    output_files: list[Path] = []
    base_name = f"{stem}__{writer_set.pair.suffix}"
    if "csv" in requested_formats:
        csv_path = output_dir / f"{base_name}.csv"
        writer_set.writers.append(_CsvWriter(csv_path, writer_set.pair.columns))
        output_files.append(csv_path)
    if "xlsx" in requested_formats:
        xlsx_path = output_dir / f"{base_name}.xlsx"
        writer_set.writers.append(_XlsxWriter(xlsx_path, writer_set.pair.columns))
        output_files.append(xlsx_path)
    if "tmx" in requested_formats:
        tmx_path = output_dir / f"{base_name}.tmx"
        writer_set.tmx_writer = _TmxWriter(tmx_path, analysis)
        output_files.append(tmx_path)
    return output_files


class _RowWriter:
    def __init__(self, output_path: Path, columns: list[str]) -> None:
        self.output_path = output_path
        self.columns = columns

    def write_row(self, row: dict[str, str]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def abort(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class _CsvWriter(_RowWriter):
    def __init__(self, output_path: Path, columns: list[str]) -> None:
        super().__init__(output_path, columns)
        self._file = output_path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=columns)
        self._writer.writeheader()

    def write_row(self, row: dict[str, str]) -> None:
        self._writer.writerow(row)

    def close(self) -> None:
        self._file.close()


class _XlsxWriter(_RowWriter):
    def __init__(self, output_path: Path, columns: list[str]) -> None:
        super().__init__(output_path, columns)
        self._workbook = Workbook(write_only=True)
        self._sheet = self._workbook.create_sheet(title="TMX")
        self._sheet.append(columns)
        self._closed = False

    def write_row(self, row: dict[str, str]) -> None:
        self._sheet.append([row.get(column, "") for column in self.columns])

    def close(self) -> None:
        if self._closed:
            return
        self._workbook.save(self.output_path)
        self._closed = True


class _TmxWriter:
    def __init__(self, output_path: Path, analysis: TmxAnalysis) -> None:
        self.output_path = output_path
        self._file = output_path.open("w", encoding="utf-8", newline="\n")
        self._closed = False
        self._has_written_tu = False
        self._pending_separator = "\n"
        self._body_indent = analysis.body_indent
        if analysis.prefix_text:
            self._file.write(analysis.prefix_text)
        else:
            self._file.write('<?xml version="1.0" encoding="utf-8"?>\n')
            self._file.write('<tmx version="1.4">\n')
            header = ET.Element("header", analysis.header_attrs or {"srclang": analysis.srclang, "datatype": analysis.datatype})
            self._file.write(f"{ET.tostring(header, encoding='unicode', short_empty_elements=False)}\n")
            self._file.write("<body>\n")

    def write_tu(self, tu: ET.Element, source_lang: str, target_lang: str) -> None:
        pair_tu_data = _build_pair_tu(tu, source_lang, target_lang)
        if pair_tu_data is None:
            return
        pair_tu, separator = pair_tu_data
        if self._has_written_tu:
            self._file.write(self._pending_separator or "\n")
        self._file.write(ET.tostring(pair_tu, encoding="unicode", short_empty_elements=True))
        self._pending_separator = separator or "\n"
        self._has_written_tu = True

    def close(self) -> None:
        if self._closed:
            return
        if self._has_written_tu:
            self._file.write("\n")
        self._file.write(f"{self._body_indent}</body>\n</tmx>\n")
        self._file.close()
        self._closed = True

    def abort(self) -> None:
        try:
            if not self._closed:
                self._file.close()
                self._closed = True
        except Exception:
            pass


def _build_pair_tu(tu: ET.Element, source_lang: str, target_lang: str) -> tuple[ET.Element, str] | None:
    keep_langs = {source_lang, target_lang}
    pair_tu = deepcopy(tu)
    separator = pair_tu.tail or "\n"
    pair_tu.tail = None
    original_children = list(pair_tu)
    original_last_tail = original_children[-1].tail if original_children else None

    for child in list(pair_tu):
        if child.tag == "tuv" and _get_lang(child) not in keep_langs:
            pair_tu.remove(child)

    remaining_tuv_langs = {_get_lang(child) for child in pair_tu if child.tag == "tuv"}
    if not keep_langs.issubset(remaining_tuv_langs):
        return None

    remaining_children = list(pair_tu)
    if remaining_children and original_last_tail is not None:
        remaining_children[-1].tail = original_last_tail
    return pair_tu, separator


def _read_prefix_text(path: Path) -> str:
    tu_pattern = re.compile(r"<tu(?=[\s>])")
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        parts: list[str] = []
        for line in file_obj:
            match = tu_pattern.search(line)
            if match is not None:
                parts.append(line[: match.start()])
                break
            else:
                parts.append(line)
    return "".join(parts)


def _extract_body_indent(prefix_text: str) -> str:
    for line in reversed(prefix_text.splitlines()):
        body_index = line.find("<body")
        if body_index >= 0:
            return line[:body_index]
    return ""
