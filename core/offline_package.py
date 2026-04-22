"""Offline .tmrepair package export/import helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    from openpyxl.styles import Alignment, Font, PatternFill, Protection
    from openpyxl.worksheet.datavalidation import DataValidation

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
        "Предлагаемая правка",
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
        sheet.cell(row=row, column=1, value=str(issue.get("id", "")))
        sheet.cell(row=row, column=2, value=str(issue.get("check_type_label", issue.get("check_type", ""))))
        sheet.cell(row=row, column=3, value=str(issue.get("original_src", "")))
        sheet.cell(row=row, column=4, value=str(issue.get("original_tgt", "")))
        sheet.cell(row=row, column=5, value=str(issue.get("message", "")))
        sheet.cell(row=row, column=6, value=_proposed_edit_text(issue))
        sheet.cell(row=row, column=7, value="pending")
        sheet.cell(row=row, column=8, value=str(issue.get("comment", "")))
        for col_index in range(1, len(headers) + 1):
            sheet.cell(row=row, column=col_index).alignment = wrap

    decision_validation = DataValidation(type="list", formula1='"accept,reject,skip,pending"', allow_blank=True)
    sheet.add_data_validation(decision_validation)
    if sheet.max_row >= start_data_row:
        decision_validation.add(f"G{start_data_row}:G{sheet.max_row}")

    # Lock everything except "Решение" and "Комментарий".
    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row, min_col=1, max_col=8):
        for cell in row:
            cell.protection = Protection(locked=True)
    for row in range(start_data_row, sheet.max_row + 1):
        sheet.cell(row=row, column=7).protection = Protection(locked=False)
        sheet.cell(row=row, column=8).protection = Protection(locked=False)

    sheet.protection.sheet = True
    sheet.protection.enable()

    widths = {
        "A": 34,
        "B": 28,
        "C": 46,
        "D": 46,
        "E": 42,
        "F": 42,
        "G": 14,
        "H": 36,
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


def _build_html_report(state: dict[str, Any], manifest: dict[str, Any]) -> str:
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

