from pathlib import Path

from main import (
    build_parser,
    _resolve_html_report_path,
    _resolve_output_path,
    _resolve_report_path,
    _resolve_xlsx_report_path,
)


def test_resolve_output_path_uses_output_dir():
    input_path = Path("sample/foo_en-fr.tmx")
    output_path = _resolve_output_path(
        input_path=input_path,
        output_override=None,
        output_dir=Path("out"),
    )
    assert str(output_path).endswith("out\\foo_en-fr_repaired.tmx")


def test_resolve_report_path_defaults_to_per_file_reports_folder_when_verify():
    input_path = Path("sample/foo.tmx")
    output_path = Path("out/foo_repaired.tmx")
    report = _resolve_report_path(
        input_path=input_path,
        output_path=output_path,
        verify_with_gemini=True,
        report_file=None,
        report_dir=None,
    )
    assert report == Path("sample/tmx-reports/foo/foo.verification.json")


def test_resolve_html_report_path_uses_per_file_subfolder_in_report_dir():
    input_path = Path("sample/bar.tmx")
    output_path = Path("out/bar_repaired.tmx")
    html_path = _resolve_html_report_path(
        input_path=input_path,
        output_path=output_path,
        html_report_file=None,
        html_report_dir=Path("html"),
    )
    assert str(html_path).endswith("sample\\html\\bar\\bar.diff-report.html")


def test_resolve_xlsx_report_path_defaults_to_per_file_reports_folder():
    input_path = Path("sample/baz.tmx")
    output_path = Path("out/baz_repaired.tmx")
    xlsx_path = _resolve_xlsx_report_path(
        input_path=input_path,
        output_path=output_path,
        xlsx_report_file=None,
        xlsx_report_dir=None,
    )
    assert xlsx_path == Path("sample/tmx-reports/baz/baz.diff-report.xlsx")


def test_parser_accepts_gemini_max_parallel():
    parser = build_parser()
    args = parser.parse_args(["--cli", "--input", "sample.tmx", "--gemini-max-parallel", "5"])
    assert args.gemini_max_parallel == 5
