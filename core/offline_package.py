"""Offline .tmrepair package export/import helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import html
import io
import json
from pathlib import Path
import re
import tempfile
from typing import Any
import zipfile

from core.plan import Proposal, RepairPlan

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
STATE_NAME = "state.json"
SOURCE_NAME = "source.tmx"
REPORT_XLSX_NAME = "report.xlsx"
REPORT_HTML_NAME = "report.html"
DECISIONS_NAME = "decisions.json"

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_SKIPPED = "skipped"

_STATUS_ORDER = {
    STATUS_ACCEPTED: 0,
    STATUS_REJECTED: 1,
    STATUS_SKIPPED: 2,
    STATUS_PENDING: 3,
}

_DECISION_TO_STATUS = {
    "accept": STATUS_ACCEPTED,
    "accepted": STATUS_ACCEPTED,
    "approve": STATUS_ACCEPTED,
    "approved": STATUS_ACCEPTED,
    "reject": STATUS_REJECTED,
    "rejected": STATUS_REJECTED,
    "decline": STATUS_REJECTED,
    "skip": STATUS_SKIPPED,
    "skipped": STATUS_SKIPPED,
    "pending": STATUS_PENDING,
}

_SERVICE_MARKUP_RULES = {
    "remove_inline_tags",
    "remove_game_markup",
    "remove_percent_wrapped_tokens",
    "context_remove_game_markup",
    "context_remove_percent_wrapped_tokens",
}

_TYPE_LABELS = {
    "split": "Split",
    "dedup_tu": "Удаление дублей",
    "normalize_spaces": "Очистка пробелов",
    "service_markup": "Удаление служебной разметки",
    "remove_garbage_segment": "Удаление мусорных TU",
}
_TYPE_BADGE_VISUALS: dict[str, tuple[str, str, str]] = {
    "service_markup": ("🟦", "Тэги", "badge-tags"),
    "dedup_tu": ("🟪", "Дубли", "badge-dups"),
    "normalize_spaces": ("🟩", "Пробелы", "badge-spaces"),
    "remove_garbage_segment": ("🟥", "Мусор", "badge-garbage"),
    "split": ("🟨", "Split", "badge-split"),
}

_TYPE_ORDER = ["service_markup", "dedup_tu", "normalize_spaces", "remove_garbage_segment", "split"]


@dataclass(slots=True)
class PackageImportResult:
    package_path: Path
    manifest: dict[str, Any]
    state: dict[str, Any]
    plan: RepairPlan
    accepted_count: int
    rejected_count: int
    skipped_count: int
    unrecognized_count: int
    decisions_source: str
    source_tmx_path: Path
    hash_mismatch_warning: str | None


def export_tmrepair_package(
    package_path: Path,
    input_tmx_path: Path,
    plan: RepairPlan,
    settings: dict[str, Any] | None = None,
) -> None:
    """Export a self-contained .tmrepair zip package."""
    source_bytes = input_tmx_path.read_bytes()
    package_id = _sha256_bytes(source_bytes)[:16]
    now_iso = _utc_now_iso()
    state = _state_from_plan(plan=plan, package_id=package_id, timestamp_iso=now_iso)
    manifest = {
        "format_version": FORMAT_VERSION,
        "package_id": package_id,
        "source_file_name": input_tmx_path.name,
        "source_sha256": _sha256_bytes(source_bytes),
        "created_at": now_iso,
        "updated_at": now_iso,
        "settings": settings or {},
    }

    xlsx_bytes = _build_xlsx_report(state=state, manifest=manifest)
    html_text = _build_html_report(state=state, manifest=manifest)

    _write_zip_atomically(
        package_path=package_path,
        replacements={
            MANIFEST_NAME: _json_bytes(manifest),
            STATE_NAME: _json_bytes(state),
            SOURCE_NAME: source_bytes,
            REPORT_XLSX_NAME: xlsx_bytes,
            REPORT_HTML_NAME: html_text.encode("utf-8"),
        },
    )


def import_tmrepair_package(
    package_path: Path,
    external_decisions_path: Path | None = None,
) -> PackageImportResult:
    """Import decisions from .tmrepair package and return an updated plan."""
    with zipfile.ZipFile(package_path, "r") as archive:
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        state = json.loads(archive.read(STATE_NAME).decode("utf-8"))
        source_bytes = archive.read(SOURCE_NAME)
        decisions_payload, decisions_source = _load_decisions_payload(
            archive=archive,
            external_decisions_path=external_decisions_path,
        )

    unrecognized_count = _apply_decisions_to_state(state=state, decisions_payload=decisions_payload)
    state["updated_at"] = _utc_now_iso()
    manifest["updated_at"] = state["updated_at"]

    _write_zip_atomically(
        package_path=package_path,
        replacements={
            MANIFEST_NAME: _json_bytes(manifest),
            STATE_NAME: _json_bytes(state),
        },
    )

    source_temp = _write_source_temp_file(
        package_path=package_path,
        source_name=str(manifest.get("source_file_name", "source.tmx")),
        source_bytes=source_bytes,
    )

    plan = _plan_from_state(state=state, input_path=str(manifest.get("source_file_name", "")))
    accepted_count, rejected_count, skipped_count = _status_counts(state)
    hash_warning = _build_hash_warning(package_path=package_path, manifest=manifest)

    return PackageImportResult(
        package_path=package_path,
        manifest=manifest,
        state=state,
        plan=plan,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        skipped_count=skipped_count,
        unrecognized_count=unrecognized_count,
        decisions_source=decisions_source,
        source_tmx_path=source_temp,
        hash_mismatch_warning=hash_warning,
    )


def _write_source_temp_file(package_path: Path, source_name: str, source_bytes: bytes) -> Path:
    source_suffix = Path(source_name).suffix or ".tmx"
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{package_path.stem}_source_",
        suffix=source_suffix,
        dir=str(package_path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with io.FileIO(fd, "wb", closefd=True) as stream:
            stream.write(source_bytes)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def _load_decisions_payload(
    archive: zipfile.ZipFile,
    external_decisions_path: Path | None,
) -> tuple[list[dict[str, str]], str]:
    names = set(archive.namelist())
    if DECISIONS_NAME in names:
        payload = archive.read(DECISIONS_NAME).decode("utf-8")
        return _parse_decisions_json(payload), DECISIONS_NAME

    if REPORT_XLSX_NAME in names:
        return _parse_decisions_xlsx(archive.read(REPORT_XLSX_NAME)), REPORT_XLSX_NAME

    if REPORT_HTML_NAME in names:
        html_payload = archive.read(REPORT_HTML_NAME).decode("utf-8", errors="ignore")
        html_decisions = _parse_decisions_from_html(html_payload)
        if html_decisions:
            return html_decisions, REPORT_HTML_NAME

    if external_decisions_path is not None and external_decisions_path.exists():
        suffix = external_decisions_path.suffix.lower()
        if suffix == ".json":
            return _parse_decisions_json(external_decisions_path.read_text(encoding="utf-8")), str(
                external_decisions_path
            )
        if suffix in {".xlsx", ".xlsm"}:
            return _parse_decisions_xlsx(external_decisions_path.read_bytes()), str(external_decisions_path)
    return [], "none"


def _parse_decisions_from_html(payload: str) -> list[dict[str, str]]:
    marker = re.search(
        r'<script id="tmrepair-decisions-json" type="application/json">(.*?)</script>',
        payload,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if marker is None:
        return []
    return _parse_decisions_json(marker.group(1))


def _parse_decisions_json(payload: str) -> list[dict[str, str]]:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        return []
    decisions_raw: list[Any]
    if isinstance(raw, dict):
        decisions_raw = list(raw.get("decisions", []))
    elif isinstance(raw, list):
        decisions_raw = raw
    else:
        return []

    parsed: list[dict[str, str]] = []
    for item in decisions_raw:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("id", "")).strip()
        if not issue_id:
            continue
        decision = str(item.get("decision", "")).strip().lower()
        comment = str(item.get("comment", "")).strip()
        parsed.append({"id": issue_id, "decision": decision, "comment": comment})
    return parsed


def _parse_decisions_xlsx(payload: bytes) -> list[dict[str, str]]:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(payload), data_only=True)
    try:
        worksheet = workbook["Review"] if "Review" in workbook.sheetnames else workbook.active
        header_row = None
        header_map: dict[str, int] = {}
        for row_index in range(1, min(30, worksheet.max_row) + 1):
            values = [str(worksheet.cell(row=row_index, column=col).value or "").strip() for col in range(1, 20)]
            if "ID проблемы" in values and "Решение" in values:
                header_row = row_index
                for col_index, value in enumerate(values, start=1):
                    if value:
                        header_map[value] = col_index
                break
        if header_row is None:
            return []

        id_col = header_map.get("ID проблемы")
        decision_col = header_map.get("Решение")
        comment_col = header_map.get("Комментарий")
        if id_col is None or decision_col is None:
            return []

        decisions: list[dict[str, str]] = []
        for row_index in range(header_row + 1, worksheet.max_row + 1):
            issue_id = str(worksheet.cell(row=row_index, column=id_col).value or "").strip()
            if not issue_id:
                continue
            decision = str(worksheet.cell(row=row_index, column=decision_col).value or "").strip().lower()
            comment = ""
            if comment_col is not None:
                comment = str(worksheet.cell(row=row_index, column=comment_col).value or "").strip()
            decisions.append({"id": issue_id, "decision": decision, "comment": comment})
        return decisions
    finally:
        workbook.close()


def _apply_decisions_to_state(state: dict[str, Any], decisions_payload: list[dict[str, str]]) -> int:
    issues = state.get("issues", [])
    if not isinstance(issues, list):
        return 0
    by_id: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_id = str(issue.get("id", "")).strip()
        if issue_id:
            by_id[issue_id] = issue

    unrecognized = 0
    for decision in decisions_payload:
        issue_id = str(decision.get("id", "")).strip()
        if not issue_id:
            continue
        issue = by_id.get(issue_id)
        if issue is None:
            unrecognized += 1
            continue
        normalized = _normalize_status(decision.get("decision", ""))
        if normalized is None:
            unrecognized += 1
            continue
        issue["status"] = normalized
        comment = str(decision.get("comment", "")).strip()
        if comment:
            issue["comment"] = comment
    return unrecognized


def _normalize_status(value: str | Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return _DECISION_TO_STATUS.get(normalized)


def _status_counts(state: dict[str, Any]) -> tuple[int, int, int]:
    accepted = 0
    rejected = 0
    skipped = 0
    for issue in state.get("issues", []):
        if not isinstance(issue, dict):
            continue
        status = str(issue.get("status", "")).strip().lower()
        if status == STATUS_ACCEPTED:
            accepted += 1
        elif status == STATUS_REJECTED:
            rejected += 1
        elif status == STATUS_SKIPPED:
            skipped += 1
    return accepted, rejected, skipped


def _build_hash_warning(package_path: Path, manifest: dict[str, Any]) -> str | None:
    source_name = str(manifest.get("source_file_name", "")).strip()
    source_hash = str(manifest.get("source_sha256", "")).strip().lower()
    if not source_name or not source_hash:
        return None
    disk_path = package_path.parent / source_name
    if not disk_path.exists():
        return None
    disk_hash = _sha256_file(disk_path)
    if disk_hash.lower() == source_hash:
        return None
    return (
        f"TMX hash mismatch for {disk_path.name}: "
        f"package={source_hash}, disk={disk_hash}. "
        "Package source will be used for apply."
    )


def _state_from_plan(plan: RepairPlan, package_id: str, timestamp_iso: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for proposal in plan.proposals:
        issues.append(_issue_from_proposal(proposal))
    issues.sort(key=lambda item: (_STATUS_ORDER.get(str(item.get("status", "")), 99), int(item.get("tu_index", 0))))
    return {
        "package_id": package_id,
        "created_at": timestamp_iso,
        "updated_at": timestamp_iso,
        "total_tus": int(plan.total_tus),
        "issues": issues,
    }


def _issue_from_proposal(proposal: Proposal) -> dict[str, Any]:
    check_type = _proposal_check_type(proposal)
    return {
        "id": proposal.proposal_id,
        "kind": proposal.kind,
        "check_type": check_type,
        "check_type_label": _type_label(check_type),
        "tu_index": int(proposal.tu_index),
        "status": STATUS_PENDING,
        "comment": "",
        "confidence": proposal.confidence,
        "gemini_verdict": proposal.gemini_verdict,
        "rule": proposal.rule,
        "message": proposal.message,
        "before_src": proposal.before_src,
        "after_src": proposal.after_src,
        "before_tgt": proposal.before_tgt,
        "after_tgt": proposal.after_tgt,
        "original_src": proposal.original_src,
        "original_tgt": proposal.original_tgt,
        "src_parts": list(proposal.src_parts),
        "tgt_parts": list(proposal.tgt_parts),
    }


def _proposal_check_type(proposal: Proposal) -> str:
    if proposal.kind == "split":
        return "split"
    rule = proposal.rule or "cleanup"
    if rule in _SERVICE_MARKUP_RULES:
        return "service_markup"
    return rule


def _type_label(check_type: str) -> str:
    return _TYPE_LABELS.get(check_type, check_type)


def _plan_from_state(state: dict[str, Any], input_path: str) -> RepairPlan:
    proposals: list[Proposal] = []
    for issue in state.get("issues", []):
        if not isinstance(issue, dict):
            continue
        status = str(issue.get("status", STATUS_PENDING)).strip().lower()
        accepted = status == STATUS_ACCEPTED
        kind = str(issue.get("kind", "cleanup")).strip() or "cleanup"
        if kind not in {"split", "cleanup"}:
            kind = "cleanup"
        proposals.append(
            Proposal(
                proposal_id=str(issue.get("id", "")),
                kind=kind,
                tu_index=int(issue.get("tu_index", 0) or 0),
                accepted=accepted,
                confidence=str(issue.get("confidence", "")),
                gemini_verdict=str(issue.get("gemini_verdict", "")),
                src_parts=list(issue.get("src_parts", []) or []),
                tgt_parts=list(issue.get("tgt_parts", []) or []),
                rule=str(issue.get("rule", "")),
                message=str(issue.get("message", "")),
                before_src=str(issue.get("before_src", "")),
                after_src=str(issue.get("after_src", "")),
                before_tgt=str(issue.get("before_tgt", "")),
                after_tgt=str(issue.get("after_tgt", "")),
                original_src=str(issue.get("original_src", "")),
                original_tgt=str(issue.get("original_tgt", "")),
            )
        )
    total_tus = int(state.get("total_tus", 0) or 0)
    if total_tus <= 0 and proposals:
        total_tus = max(p.tu_index for p in proposals) + 1
    return RepairPlan(input_path=input_path, total_tus=total_tus, proposals=proposals)


def _build_xlsx_report(state: dict[str, Any], manifest: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Alignment, Font, PatternFill, Protection
    from openpyxl.worksheet.datavalidation import DataValidation

    def _build_xlsx_review_cell(
        before_text: str, after_text: str, *, whitespace_focused: bool
    ) -> CellRichText:
        normal_font = InlineFont(strike=False)
        deleted_font = InlineFont(color="00B91C1C", strike=True)
        added_font = InlineFont(color="0015803D")

        before_segments, after_segments = _diff_segments(
            before_text, after_text, by_char=True
        )
        rich = CellRichText()

        def _append_run(text: str, font: InlineFont | None = None) -> None:
            if not text:
                return
            if font is None:
                rich.append(text)
            else:
                rich.append(TextBlock(font, text))

        before_index = 0
        after_index = 0
        before_len = len(before_segments)
        after_len = len(after_segments)
        while before_index < before_len or after_index < after_len:
            if before_index < before_len:
                before_kind, before_text_part = before_segments[before_index]
                if before_kind == "eq":
                    _append_run(before_text_part, normal_font)
                    before_index += 1
                    after_index += 1
                    continue
                if before_kind == "del":
                    rendered = (
                        before_text_part.replace(" ", "·")
                        if whitespace_focused
                        else before_text_part
                    )
                    _append_run(rendered, deleted_font)
                    before_index += 1
                    continue
                before_index += 1

            if after_index < after_len:
                after_kind, after_text_part = after_segments[after_index]
                if after_kind == "add":
                    rendered = (
                        after_text_part.replace(" ", "·")
                        if whitespace_focused
                        else after_text_part
                    )
                    _append_run(rendered, added_font)
                    after_index += 1
                    continue
                if after_kind == "eq":
                    _append_run(after_text_part, normal_font)
                    after_index += 1
                    continue
                after_index += 1
        return rich

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Review"

    sheet["A1"] = "tmrepair_package_id"
    sheet["B1"] = str(manifest.get("package_id", ""))
    sheet["A2"] = "tmx_sha256"
    sheet["B2"] = str(manifest.get("source_sha256", ""))
    sheet["A3"] = "source_file_name"
    sheet["B3"] = str(manifest.get("source_file_name", ""))

    headers = [
        "ID проблемы",
        "Тип проверки",
        "TU source",
        "TU target",
        "Описание",
        "Решение",
        "Комментарий",
    ]
    header_row = 4
    for col_index, title in enumerate(headers, start=1):
        sheet.cell(row=header_row, column=col_index, value=title)

    header_fill = PatternFill(start_color="DCEBFF", end_color="DCEBFF", fill_type="solid")
    header_font = Font(bold=True, color="0F172A")
    wrap = Alignment(vertical="top", wrap_text=True)

    for col_index in range(1, len(headers) + 1):
        cell = sheet.cell(row=header_row, column=col_index)
        cell.fill = header_fill
        cell.font = header_font

    start_data_row = header_row + 1
    for row_offset, issue in enumerate(state.get("issues", []), start=0):
        row = start_data_row + row_offset
        if not isinstance(issue, dict):
            continue
        before_src, after_src, before_tgt, after_tgt = _issue_before_after_text(issue)
        check_type = str(issue.get("check_type", "")).strip()
        whitespace_focused = check_type == "normalize_spaces"

        sheet.cell(row=row, column=1, value=str(issue.get("id", "")))
        sheet.cell(row=row, column=2, value=str(issue.get("check_type_label", issue.get("check_type", ""))))
        sheet.cell(
            row=row,
            column=3,
            value=_build_xlsx_review_cell(
                before_src, after_src, whitespace_focused=whitespace_focused
            ),
        )
        sheet.cell(
            row=row,
            column=4,
            value=_build_xlsx_review_cell(
                before_tgt, after_tgt, whitespace_focused=whitespace_focused
            ),
        )
        sheet.cell(row=row, column=5, value=str(issue.get("message", "")))
        sheet.cell(row=row, column=6, value="pending")
        sheet.cell(row=row, column=7, value=str(issue.get("comment", "")))
        for col_index in range(1, len(headers) + 1):
            sheet.cell(row=row, column=col_index).alignment = wrap

    decision_validation = DataValidation(type="list", formula1='"accept,reject,skip,pending"', allow_blank=True)
    sheet.add_data_validation(decision_validation)
    if sheet.max_row >= start_data_row:
        decision_validation.add(f"F{start_data_row}:F{sheet.max_row}")

    # Lock everything except "Решение" and "Комментарий".
    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, min_col=1, max_col=len(headers)):
        for cell in row:
            cell.protection = Protection(locked=True)
    for row in range(start_data_row, sheet.max_row + 1):
        sheet.cell(row=row, column=6).protection = Protection(locked=False)
        sheet.cell(row=row, column=7).protection = Protection(locked=False)

    sheet.protection.sheet = True
    sheet.protection.enable()

    widths = {
        "A": 34,
        "B": 28,
        "C": 46,
        "D": 46,
        "E": 42,
        "F": 14,
        "G": 36,
    }
    for key, width in widths.items():
        sheet.column_dimensions[key].width = width
    sheet.freeze_panes = "A5"

    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def _proposed_edit_text(issue: dict[str, Any]) -> str:
    kind = str(issue.get("kind", ""))
    if kind == "split":
        src_parts = list(issue.get("src_parts", []) or [])
        tgt_parts = list(issue.get("tgt_parts", []) or [])
        pairs = []
        for index, (src, tgt) in enumerate(zip(src_parts, tgt_parts), start=1):
            pairs.append(f"{index}. {src} -> {tgt}")
        return "\n".join(pairs)
    after_src = str(issue.get("after_src", "")).strip()
    after_tgt = str(issue.get("after_tgt", "")).strip()
    if after_src or after_tgt:
        return f"SRC: {after_src}\nTGT: {after_tgt}".strip()
    return str(issue.get("message", ""))


def _build_html_report_legacy(state: dict[str, Any], manifest: dict[str, Any]) -> str:
    rows: list[str] = []
    for issue in state.get("issues", []):
        if not isinstance(issue, dict):
            continue
        issue_id = html.escape(str(issue.get("id", "")))
        check_label = html.escape(str(issue.get("check_type_label", issue.get("check_type", "")))
        )
        source = html.escape(str(issue.get("original_src", "")))
        target = html.escape(str(issue.get("original_tgt", "")))
        message = html.escape(str(issue.get("message", "")))
        proposed = html.escape(_proposed_edit_text(issue))
        rows.append(
            f"""
            <tr data-issue-id="{issue_id}">
              <td>{issue_id}</td>
              <td>{check_label}</td>
              <td>{source}</td>
              <td>{target}</td>
              <td>{message}</td>
              <td>{proposed}</td>
              <td>
                <select class="decision-select">
                  <option value="pending">pending</option>
                  <option value="accept">accept</option>
                  <option value="reject">reject</option>
                  <option value="skip">skip</option>
                </select>
                <div class="quick-actions">
                  <button type="button" onclick="setDecision(this, 'accept')">accept</button>
                  <button type="button" onclick="setDecision(this, 'reject')">reject</button>
                  <button type="button" onclick="setDecision(this, 'skip')">skip</button>
                </div>
              </td>
              <td><input class="comment-input" type="text" value="" /></td>
            </tr>
            """
        )
    package_id = html.escape(str(manifest.get("package_id", "")))
    source_hash = html.escape(str(manifest.get("source_sha256", "")))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TMRepair Offline Review</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f5f7fa; color: #1f2937; }}
    .wrap {{ max-width: 1440px; margin: 0 auto; padding: 16px; }}
    .meta {{ background: #ffffff; border-radius: 10px; padding: 12px; margin-bottom: 12px; }}
    table {{ width: 100%; border-collapse: collapse; background: #ffffff; border-radius: 10px; overflow: hidden; }}
    th, td {{ border: 1px solid #d5dce4; vertical-align: top; text-align: left; padding: 8px; }}
    th {{ background: #e8eef6; }}
    td {{ white-space: pre-wrap; word-break: break-word; }}
    .actions {{ display: flex; gap: 8px; margin: 12px 0; }}
    .quick-actions {{ display: flex; gap: 4px; margin-top: 6px; }}
    button {{ border: 1px solid #b7c1cc; background: #fff; border-radius: 6px; padding: 4px 8px; cursor: pointer; }}
    select, input {{ width: 100%; min-height: 28px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="meta">
      <div><strong>Package ID:</strong> {package_id}</div>
      <div><strong>TMX SHA-256:</strong> {source_hash}</div>
    </div>
    <div class="actions">
      <button type="button" onclick="downloadDecisions()">Скачать размеченный файл</button>
    </div>
    <table>
      <thead>
        <tr>
          <th>ID проблемы</th>
          <th>Тип проверки</th>
          <th>TU source</th>
          <th>TU target</th>
          <th>Описание</th>
          <th>Предлагаемая правка</th>
          <th>Решение</th>
          <th>Комментарий</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>
  </div>
  <script id="tmrepair-meta-json" type="application/json">{json.dumps({"package_id": manifest.get("package_id", ""), "source_sha256": manifest.get("source_sha256", "")}, ensure_ascii=False)}</script>
  <script>
    function setDecision(btn, value) {{
      const row = btn.closest("tr");
      const select = row.querySelector(".decision-select");
      if (select) select.value = value;
    }}

    function collectDecisions() {{
      const rows = document.querySelectorAll("tbody tr[data-issue-id]");
      const decisions = [];
      rows.forEach((row) => {{
        const id = row.getAttribute("data-issue-id");
        const decision = row.querySelector(".decision-select")?.value || "pending";
        const comment = row.querySelector(".comment-input")?.value || "";
        decisions.push({{ id, decision, comment }});
      }});
      return decisions;
    }}

    function downloadDecisions() {{
      const meta = JSON.parse(document.getElementById("tmrepair-meta-json").textContent || "{{}}");
      const payload = {{
        package_id: meta.package_id || "",
        source_sha256: meta.source_sha256 || "",
        decisions: collectDecisions()
      }};
      const blob = new Blob([JSON.stringify(payload, null, 2)], {{ type: "application/json;charset=utf-8" }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "decisions.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }}
  </script>
</body>
</html>
"""


def _status_to_decision_value(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == STATUS_ACCEPTED:
        return "accept"
    if normalized == STATUS_REJECTED:
        return "reject"
    return "pending"


def _issue_before_after_text(issue: dict[str, Any]) -> tuple[str, str, str, str]:
    kind = str(issue.get("kind", "")).strip()
    if kind == "split":
        before_src = str(issue.get("original_src", ""))
        before_tgt = str(issue.get("original_tgt", ""))
        after_src = "\n".join(str(part) for part in list(issue.get("src_parts", []) or []))
        after_tgt = "\n".join(str(part) for part in list(issue.get("tgt_parts", []) or []))
        return before_src, after_src, before_tgt, after_tgt
    before_src = str(issue.get("before_src", "") or issue.get("original_src", ""))
    after_src = str(issue.get("after_src", ""))
    before_tgt = str(issue.get("before_tgt", "") or issue.get("original_tgt", ""))
    after_tgt = str(issue.get("after_tgt", ""))
    return before_src, after_src, before_tgt, after_tgt


def _tokenize_diff_text(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"\w+|\s+|[^\w\s]", text, flags=re.UNICODE)


def _diff_segments(
    before_text: str, after_text: str, *, by_char: bool = False
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    if by_char:
        before_units = list(before_text)
        after_units = list(after_text)
    else:
        before_units = _tokenize_diff_text(before_text)
        after_units = _tokenize_diff_text(after_text)
    matcher = SequenceMatcher(a=before_units, b=after_units, autojunk=False)
    before_segments: list[tuple[str, str]] = []
    after_segments: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = "".join(before_units[i1:i2])
            if text:
                before_segments.append(("eq", text))
                after_segments.append(("eq", text))
        elif tag == "delete":
            text = "".join(before_units[i1:i2])
            if text:
                before_segments.append(("del", text))
        elif tag == "insert":
            text = "".join(after_units[j1:j2])
            if text:
                after_segments.append(("add", text))
        elif tag == "replace":
            deleted = "".join(before_units[i1:i2])
            added = "".join(after_units[j1:j2])
            if deleted:
                before_segments.append(("del", deleted))
            if added:
                after_segments.append(("add", added))
    return before_segments, after_segments


def _render_diff_segment_text(text: str, *, mark_spaces: bool) -> str:
    escaped = html.escape(text)
    if not mark_spaces:
        return escaped
    return escaped.replace(" ", '<span class="diff-space" title="SPACE">·</span>')


def _segments_to_html(segments: list[tuple[str, str]], *, mark_changed_spaces: bool = False) -> str:
    if not segments:
        return '<span class="diff-empty">∅</span>'
    class_by_kind = {"eq": "diff-eq", "del": "diff-del", "add": "diff-add"}
    parts: list[str] = []
    for kind, text in segments:
        class_name = class_by_kind.get(kind, "diff-eq")
        mark_spaces = mark_changed_spaces and kind in {"del", "add"}
        rendered_text = _render_diff_segment_text(text, mark_spaces=mark_spaces)
        parts.append(f'<span class="{class_name}">{rendered_text}</span>')
    return "".join(parts)


def _build_inline_diff_html(
    before_text: str, after_text: str, *, whitespace_focused: bool = False
) -> tuple[str, str]:
    if before_text == after_text:
        shared = html.escape(before_text) if before_text else '<span class="diff-empty">∅</span>'
        return shared, shared
    before_segments, after_segments = _diff_segments(before_text, after_text, by_char=whitespace_focused)
    return (
        _segments_to_html(before_segments, mark_changed_spaces=whitespace_focused),
        _segments_to_html(after_segments, mark_changed_spaces=whitespace_focused),
    )


def _build_inline_diff_text(
    before_text: str, after_text: str, *, whitespace_focused: bool = False
) -> str:
    if before_text == after_text:
        return "No changes"
    before_segments, after_segments = _diff_segments(before_text, after_text, by_char=whitespace_focused)

    def _compose_line(segments: list[tuple[str, str]], changed_kind: str) -> str:
        parts: list[str] = []
        for kind, text in segments:
            if kind == "eq":
                parts.append(text)
                continue
            if kind != changed_kind:
                continue
            rendered = text.replace(" ", "·") if whitespace_focused else text
            if changed_kind == "del":
                parts.append(f"[-{rendered}-]")
            else:
                parts.append(f"[+{rendered}+]")
        joined = "".join(parts)
        return joined if joined else "∅"

    before_line = _compose_line(before_segments, "del")
    after_line = _compose_line(after_segments, "add")
    return f"- {before_line}\n+ {after_line}"


def _badge_visual(check_type: str, fallback_label: str) -> tuple[str, str, str]:
    visual = _TYPE_BADGE_VISUALS.get(check_type)
    if visual is None:
        return "⬜", fallback_label, "badge-generic"
    return visual


def _ordered_type_keys(keys: set[str]) -> list[str]:
    ordered = [key for key in _TYPE_ORDER if key in keys]
    ordered.extend(sorted(key for key in keys if key not in _TYPE_ORDER))
    return ordered


def _build_html_report(state: dict[str, Any], manifest: dict[str, Any]) -> str:
    rows: list[str] = []
    initial_decisions: list[dict[str, str]] = []
    type_counts: dict[str, int] = {}

    for issue in state.get("issues", []):
        if not isinstance(issue, dict):
            continue
        issue_id_raw = str(issue.get("id", "")).strip()
        if not issue_id_raw:
            continue
        issue_id = html.escape(issue_id_raw)
        check_type = str(issue.get("check_type", "")).strip() or "cleanup"
        check_label = str(issue.get("check_type_label", issue.get("check_type", ""))).strip() or check_type
        badge_emoji, badge_text, badge_css = _badge_visual(check_type, check_label)
        badge_title = html.escape(str(issue.get("message", "")).strip() or check_label, quote=True)
        tu_display = int(issue.get("tu_index", 0) or 0) + 1
        decision_value = _status_to_decision_value(issue.get("status", STATUS_PENDING))

        before_src, after_src, before_tgt, after_tgt = _issue_before_after_text(issue)
        whitespace_focused = check_type == "normalize_spaces"
        src_before_html, src_after_html = _build_inline_diff_html(
            before_src, after_src, whitespace_focused=whitespace_focused
        )
        tgt_before_html, tgt_after_html = _build_inline_diff_html(
            before_tgt, after_tgt, whitespace_focused=whitespace_focused
        )

        accept_active = " active" if decision_value == "accept" else ""
        reject_active = " active" if decision_value == "reject" else ""
        rows.append(
            f"""
            <tr data-issue-id="{issue_id}" data-check-type="{html.escape(check_type, quote=True)}" data-decision="{html.escape(decision_value, quote=True)}">
              <td class="col-issue">
                <div class="issue-id">{issue_id}</div>
                <div class="issue-tu">TU #{tu_display}</div>
                <span class="type-badge {badge_css}" title="{badge_title}">{badge_emoji} {html.escape(badge_text)}</span>
              </td>
              <td class="col-before">
                <div class="lang-block">
                  <div class="lang-label">SRC</div>
                  <div class="diff-text">{src_before_html}</div>
                </div>
                <div class="lang-block">
                  <div class="lang-label">TGT</div>
                  <div class="diff-text">{tgt_before_html}</div>
                </div>
              </td>
              <td class="col-after">
                <div class="lang-block">
                  <div class="lang-label">SRC</div>
                  <div class="diff-text">{src_after_html}</div>
                </div>
                <div class="lang-block">
                  <div class="lang-label">TGT</div>
                  <div class="diff-text">{tgt_after_html}</div>
                </div>
              </td>
              <td class="col-decision">
                <div class="decision-buttons">
                  <button type="button" class="decision-btn decision-accept{accept_active}" data-value="accept" aria-label="Принять" title="Принять">✓</button>
                  <button type="button" class="decision-btn decision-reject{reject_active}" data-value="reject" aria-label="Отклонить" title="Отклонить">✕</button>
                </div>
              </td>
            </tr>
            """
        )

        type_counts[check_type] = type_counts.get(check_type, 0) + 1
        initial_decisions.append({"id": issue_id_raw, "decision": decision_value})

    type_filters: list[str] = []
    for check_type in _ordered_type_keys(set(type_counts)):
        count = type_counts.get(check_type, 0)
        fallback_label = _type_label(check_type)
        badge_emoji, badge_text, badge_css = _badge_visual(check_type, fallback_label)
        type_filters.append(
            f"""
            <label class="type-filter {badge_css}">
              <input type="checkbox" value="{html.escape(check_type, quote=True)}" checked />
              <span class="type-filter-label">{badge_emoji} {html.escape(badge_text)}</span>
              <span class="type-filter-count">{count}</span>
            </label>
            """
        )

    template = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TMRepair Offline Review</title>
  <style>
    :root {
      --bg: #f3f5f8;
      --surface: #ffffff;
      --muted: #64748b;
      --line: #d7dee8;
      --accept-bg: #dcfce7;
      --accept-fg: #166534;
      --reject-bg: #fee2e2;
      --reject-fg: #991b1b;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: var(--bg); color: #0f172a; }
    .wrap { max-width: 1560px; margin: 0 auto; padding: 14px; }
    .toolbar {
      position: sticky; top: 0; z-index: 25;
      background: linear-gradient(180deg, rgba(243,245,248,0.98), rgba(243,245,248,0.9));
      border-bottom: 1px solid #d5dde7; border-radius: 10px;
      padding: 10px 10px 12px; margin-bottom: 10px;
    }
    .toolbar-top { display: flex; gap: 12px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
    .progress-wrap { min-width: 320px; flex: 1; }
    .progress-text { font-weight: 600; margin-bottom: 6px; }
    .progress-track { width: 100%; height: 9px; border-radius: 999px; background: #d8e1ea; overflow: hidden; }
    .progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, #22c55e, #16a34a); }
    .counter-line { margin-top: 8px; color: #334155; font-weight: 600; }
    .toolbar-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .type-and-bulk { margin-top: 10px; display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; flex-wrap: wrap; }
    .type-filters { display: flex; flex-wrap: wrap; gap: 8px; }
    .type-filter { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); border-radius: 999px; background: #fff; padding: 4px 10px; font-size: 13px; }
    .type-filter input { margin: 0; }
    .type-filter-count { color: var(--muted); font-weight: 700; }
    .bulk-actions { display: flex; gap: 6px; flex-wrap: wrap; }
    button { border: 1px solid #b8c2cf; background: #fff; color: #0f172a; border-radius: 8px; padding: 7px 11px; cursor: pointer; font-weight: 600; }
    button:hover { background: #f8fafc; }
    .btn-primary { background: #0f766e; border-color: #0f766e; color: #fff; }
    .btn-primary:hover { background: #0b5f59; }
    .meta { margin-top: 8px; }
    .meta details { background: #e8edf3; border-radius: 8px; padding: 7px 10px; color: #334155; }
    .meta summary { cursor: pointer; font-weight: 700; }
    .meta-code { font-family: Consolas, "Courier New", monospace; font-size: 12px; word-break: break-all; }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
    th, td { border: 1px solid var(--line); vertical-align: top; text-align: left; padding: 10px; }
    th { background: #e8eef6; font-size: 13px; }
    .col-issue { width: 220px; }
    .col-before, .col-after { width: 38%; }
    .col-decision { width: 260px; }
    .issue-id { font-weight: 700; font-family: Consolas, "Courier New", monospace; font-size: 12px; margin-bottom: 4px; }
    .issue-tu { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
    .type-badge { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 4px 8px; font-size: 12px; font-weight: 700; }
    .badge-tags { background: #dbeafe; color: #1d4ed8; }
    .badge-dups { background: #ede9fe; color: #6d28d9; }
    .badge-spaces { background: #dcfce7; color: #15803d; }
    .badge-garbage { background: #fee2e2; color: #b91c1c; }
    .badge-split { background: #fef3c7; color: #b45309; }
    .badge-generic { background: #e2e8f0; color: #334155; }
    .lang-block { border: 1px solid #dbe2ea; background: #f8fafc; border-radius: 8px; padding: 8px; margin-bottom: 7px; }
    .lang-block:last-child { margin-bottom: 0; }
    .lang-label { font-size: 11px; color: #475569; font-weight: 700; margin-bottom: 4px; letter-spacing: 0.06em; }
    .diff-text { font-family: Consolas, "Courier New", monospace; font-size: 13px; line-height: 1.35; white-space: pre-wrap; word-break: break-word; }
    .diff-eq { color: #0f172a; }
    .diff-del { color: var(--reject-fg); background: var(--reject-bg); text-decoration: line-through; border-radius: 3px; padding: 0 1px; }
    .diff-add { color: var(--accept-fg); background: var(--accept-bg); text-decoration: underline; border-radius: 3px; padding: 0 1px; }
    .diff-space { display: inline-block; min-width: 0.55em; text-align: center; border-radius: 2px; background: rgba(15, 23, 42, 0.12); }
    .diff-empty { color: #94a3b8; font-style: italic; }
    .decision-buttons { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
    .decision-btn { width: 34px; min-width: 34px; padding: 6px 0; font-size: 16px; line-height: 1; }
    .decision-btn.active { box-shadow: inset 0 0 0 2px rgba(15, 23, 42, 0.15); }
    .decision-accept.active { background: var(--accept-bg); color: var(--accept-fg); border-color: #22c55e; }
    .decision-reject.active { background: var(--reject-bg); color: var(--reject-fg); border-color: #ef4444; }
    tr.current { outline: 2px solid #0284c7; outline-offset: -2px; }
    tr.is-pending .issue-id::after { content: " • pending"; color: #64748b; font-weight: 600; font-size: 11px; }
    .draft-stamp { color: #64748b; font-size: 12px; margin-top: 6px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="toolbar">
      <div class="toolbar-top">
        <div class="progress-wrap">
          <div id="progress-text" class="progress-text"></div>
          <div class="progress-track"><div id="progress-fill" class="progress-fill"></div></div>
        </div>
        <div class="toolbar-actions">
          <button type="button" id="next-pending">К следующему pending</button>
          <button type="button" id="download-decisions" class="btn-primary">Скачать размеченный файл</button>
        </div>
      </div>
      <div id="decision-counters" class="counter-line"></div>
      <div class="type-and-bulk">
        <div class="type-filters">__TYPE_FILTERS__</div>
        <div class="bulk-actions">
          <button type="button" id="accept-filtered">Принять все отфильтрованные</button>
          <button type="button" id="reject-filtered">Отклонить все отфильтрованные</button>
          <button type="button" id="accept-pending">Принять все pending</button>
        </div>
      </div>
      <div id="draft-stamp" class="draft-stamp"></div>
    </div>
    <div class="meta">
      <details>
        <summary>ℹ Техническая информация</summary>
        <div><strong>Package ID:</strong> <span class="meta-code">__PACKAGE_ID__</span></div>
        <div><strong>TMX SHA-256:</strong> <span class="meta-code">__SOURCE_HASH__</span></div>
      </details>
    </div>
    <table>
      <thead>
        <tr>
          <th>Правка</th>
          <th>До</th>
          <th>После</th>
          <th>Решение</th>
        </tr>
      </thead>
      <tbody>
        __ROWS__
      </tbody>
    </table>
  </div>
  <script id="tmrepair-meta-json" type="application/json">__META_JSON__</script>
  <script id="tmrepair-decisions-json" type="application/json">__DECISIONS_JSON__</script>
  <script>
    (() => {
      const rows = Array.from(document.querySelectorAll("tbody tr[data-issue-id]"));
      const typeInputs = Array.from(document.querySelectorAll(".type-filter input[type='checkbox']"));
      const progressText = document.getElementById("progress-text");
      const progressFill = document.getElementById("progress-fill");
      const counterLine = document.getElementById("decision-counters");
      const draftStamp = document.getElementById("draft-stamp");
      const decisionsScript = document.getElementById("tmrepair-decisions-json");
      const meta = JSON.parse(document.getElementById("tmrepair-meta-json").textContent || "{}");
      const storageKey = `tmrepair:draft:${meta.package_id || ""}:${meta.source_sha256 || ""}`;
      const knownDecisions = new Set(["accept", "reject", "pending"]);
      let currentRow = null;
      let saveTimer = null;

      function getDecision(row) {
        const value = (row.dataset.decision || "pending").toLowerCase();
        return knownDecisions.has(value) ? value : "pending";
      }

      function setDecision(row, decision, shouldPersist = true) {
        const normalized = knownDecisions.has(decision) ? decision : "pending";
        row.dataset.decision = normalized;
        row.classList.toggle("is-pending", normalized === "pending");
        row.querySelectorAll(".decision-btn").forEach((btn) => {
          const active = btn.dataset.value === normalized;
          btn.classList.toggle("active", active);
        });
        if (shouldPersist) {
          updateSummary();
          syncDecisionsScript();
          scheduleDraftSave();
        }
      }

      function getVisibleRows() {
        return rows.filter((row) => !row.hidden);
      }

      function setCurrentRow(row, shouldScroll = false) {
        if (currentRow) {
          currentRow.classList.remove("current");
        }
        currentRow = row || null;
        if (!currentRow) {
          return;
        }
        currentRow.classList.add("current");
        if (shouldScroll) {
          currentRow.scrollIntoView({ block: "center", behavior: "smooth" });
        }
      }

      function ensureCurrentRow() {
        const visible = getVisibleRows();
        if (!visible.length) {
          setCurrentRow(null);
          return;
        }
        if (currentRow && !currentRow.hidden) {
          return;
        }
        const pending = visible.find((row) => getDecision(row) === "pending");
        setCurrentRow(pending || visible[0]);
      }

      function countDecisions(sourceRows) {
        const counts = { accept: 0, reject: 0, pending: 0 };
        sourceRows.forEach((row) => {
          counts[getDecision(row)] += 1;
        });
        return counts;
      }

      function updateSummary() {
        const total = rows.length;
        const counts = countDecisions(rows);
        const done = total - counts.pending;
        const progress = total > 0 ? Math.round((done / total) * 100) : 0;
        progressFill.style.width = `${progress}%`;
        progressText.textContent = `Размечено ${done} из ${total} — осталось ${counts.pending} pending`;
        counterLine.textContent = `✓ ${counts.accept} / ✗ ${counts.reject} / pending ${counts.pending}`;
      }

      function applyTypeFilters() {
        const enabled = new Set(typeInputs.filter((input) => input.checked).map((input) => input.value));
        rows.forEach((row) => {
          row.hidden = !enabled.has(row.dataset.checkType || "");
        });
        ensureCurrentRow();
      }

      function applyDecisionToVisible(decision, pendingOnly = false) {
        getVisibleRows().forEach((row) => {
          if (pendingOnly && getDecision(row) !== "pending") {
            return;
          }
          setDecision(row, decision, false);
        });
        updateSummary();
        syncDecisionsScript();
        scheduleDraftSave();
      }

      function gotoNextPending() {
        const visible = getVisibleRows();
        if (!visible.length) {
          return;
        }
        const start = currentRow ? visible.indexOf(currentRow) + 1 : 0;
        for (let offset = 0; offset < visible.length; offset += 1) {
          const row = visible[(start + offset) % visible.length];
          if (getDecision(row) === "pending") {
            setCurrentRow(row, true);
            return;
          }
        }
      }

      function moveSelection(step) {
        const visible = getVisibleRows();
        if (!visible.length) {
          return;
        }
        let index = currentRow ? visible.indexOf(currentRow) : -1;
        if (index < 0) {
          index = 0;
        } else {
          index = (index + step + visible.length) % visible.length;
        }
        setCurrentRow(visible[index], true);
      }

      function collectDecisions() {
        return rows.map((row) => ({
          id: row.dataset.issueId || "",
          decision: getDecision(row),
        }));
      }

      function syncDecisionsScript() {
        if (!decisionsScript) {
          return;
        }
        decisionsScript.textContent = JSON.stringify({ decisions: collectDecisions() }, null, 2);
      }

      function renderDraftStamp(isoTime) {
        if (!isoTime) {
          draftStamp.textContent = "";
          return;
        }
        const dt = new Date(isoTime);
        draftStamp.textContent = `Черновик сохранен: ${dt.toLocaleString()}`;
      }

      function saveDraftNow() {
        const payload = {
          updated_at: new Date().toISOString(),
          decisions: collectDecisions(),
        };
        try {
          localStorage.setItem(storageKey, JSON.stringify(payload));
          renderDraftStamp(payload.updated_at);
        } catch (_error) {
          // ignore storage errors
        }
      }

      function scheduleDraftSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(saveDraftNow, 250);
      }

      function applyDraft(payload) {
        if (!payload || !Array.isArray(payload.decisions)) {
          return;
        }
        const byId = new Map();
        payload.decisions.forEach((item) => {
          if (item && typeof item === "object") {
            byId.set(String(item.id || ""), item);
          }
        });
        rows.forEach((row) => {
          const item = byId.get(row.dataset.issueId || "");
          if (!item) {
            return;
          }
          setDecision(row, String(item.decision || "pending"), false);
        });
        updateSummary();
        syncDecisionsScript();
        renderDraftStamp(payload.updated_at || "");
      }

      function tryRestoreDraft() {
        if (!storageKey) {
          return;
        }
        let parsed = null;
        try {
          parsed = JSON.parse(localStorage.getItem(storageKey) || "null");
        } catch (_error) {
          parsed = null;
        }
        if (!parsed || !Array.isArray(parsed.decisions) || parsed.decisions.length === 0) {
          return;
        }
        if (window.confirm(`Обнаружен несохраненный черновик (${parsed.updated_at || "неизвестно"}). Восстановить?`)) {
          applyDraft(parsed);
        }
      }

      function downloadDecisions() {
        const payload = {
          package_id: meta.package_id || "",
          source_sha256: meta.source_sha256 || "",
          decisions: collectDecisions(),
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "decisions.json";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        saveDraftNow();
      }

      rows.forEach((row) => {
        setDecision(row, getDecision(row), false);
        row.addEventListener("click", () => setCurrentRow(row));
        row.querySelectorAll(".decision-btn").forEach((btn) => {
          btn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            setCurrentRow(row);
            const nextDecision = btn.dataset.value || "pending";
            const currentDecision = getDecision(row);
            setDecision(row, currentDecision === nextDecision ? "pending" : nextDecision);
          });
        });
      });

      typeInputs.forEach((input) => {
        input.addEventListener("change", () => {
          applyTypeFilters();
          updateSummary();
          syncDecisionsScript();
        });
      });

      document.getElementById("accept-filtered")?.addEventListener("click", () => applyDecisionToVisible("accept", false));
      document.getElementById("reject-filtered")?.addEventListener("click", () => applyDecisionToVisible("reject", false));
      document.getElementById("accept-pending")?.addEventListener("click", () => applyDecisionToVisible("accept", true));
      document.getElementById("next-pending")?.addEventListener("click", gotoNextPending);
      document.getElementById("download-decisions")?.addEventListener("click", downloadDecisions);

      document.addEventListener("keydown", (event) => {
        const key = event.key.toLowerCase();
        if (event.ctrlKey && key === "s") {
          event.preventDefault();
          downloadDecisions();
          return;
        }
        const tag = (event.target && event.target.tagName ? event.target.tagName : "").toLowerCase();
        if (tag === "input" || tag === "textarea" || tag === "select") {
          return;
        }
        if (key === "j" || event.key === "ArrowDown") {
          event.preventDefault();
          moveSelection(1);
        } else if (key === "k" || event.key === "ArrowUp") {
          event.preventDefault();
          moveSelection(-1);
        } else if (key === "a" && currentRow) {
          event.preventDefault();
          setDecision(currentRow, "accept");
        } else if (key === "r" && currentRow) {
          event.preventDefault();
          setDecision(currentRow, "reject");
        } else if (key === "n") {
          event.preventDefault();
          gotoNextPending();
        }
      });

      tryRestoreDraft();
      applyTypeFilters();
      updateSummary();
      syncDecisionsScript();
      ensureCurrentRow();
    })();
  </script>
</body>
</html>
"""

    package_id = html.escape(str(manifest.get("package_id", "")))
    source_hash = html.escape(str(manifest.get("source_sha256", "")))
    meta_json = json.dumps(
        {
            "package_id": str(manifest.get("package_id", "")),
            "source_sha256": str(manifest.get("source_sha256", "")),
        },
        ensure_ascii=False,
    )
    decisions_json = json.dumps({"decisions": initial_decisions}, ensure_ascii=False)

    return (
        template.replace("__ROWS__", "".join(rows))
        .replace("__TYPE_FILTERS__", "".join(type_filters))
        .replace("__PACKAGE_ID__", package_id)
        .replace("__SOURCE_HASH__", source_hash)
        .replace("__META_JSON__", meta_json)
        .replace("__DECISIONS_JSON__", decisions_json)
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _write_zip_atomically(package_path: Path, replacements: dict[str, bytes]) -> None:
    package_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = package_path.with_suffix(package_path.suffix + ".tmp")
    if package_path.exists():
        with zipfile.ZipFile(package_path, "r") as src, zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as dst:
            existing = set()
            for info in src.infolist():
                name = info.filename
                existing.add(name)
                replacement = replacements.get(name)
                if replacement is None:
                    dst.writestr(info, src.read(name))
                else:
                    dst.writestr(name, replacement)
            for name, payload in replacements.items():
                if name not in existing:
                    dst.writestr(name, payload)
    else:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            for name, payload in replacements.items():
                dst.writestr(name, payload)
    temp_path.replace(package_path)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
