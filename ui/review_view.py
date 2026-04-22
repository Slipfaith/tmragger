"""Plan review dialog for accepting/rejecting proposed edits before apply."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Callable

from app_meta import APP_NAME
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.plan import Proposal
from ui.types import FilePlanResult, PlanPhaseResult

PROPOSAL_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_ROLE = Qt.ItemDataRole.UserRole + 2
TYPE_ROLE = Qt.ItemDataRole.UserRole + 3
GROUP_ROLE = Qt.ItemDataRole.UserRole + 4

_SERVICE_MARKUP_RULES = {
    "remove_inline_tags",
    "remove_game_markup",
    "remove_percent_wrapped_tokens",
    "context_remove_game_markup",
    "context_remove_percent_wrapped_tokens",
}


@dataclass(frozen=True)
class _TypeVisual:
    label: str
    badge: str
    bg: str
    fg: str


_TYPE_VISUALS: dict[str, _TypeVisual] = {
    "service_markup": _TypeVisual("Тэги", "🟦", "#dbeafe", "#1d4ed8"),
    "dedup_tu": _TypeVisual("Дубли", "🟪", "#ede9fe", "#6d28d9"),
    "normalize_spaces": _TypeVisual("Пробелы", "🟩", "#dcfce7", "#15803d"),
    "remove_garbage_segment": _TypeVisual("Мусор", "🟥", "#fee2e2", "#b91c1c"),
    "split": _TypeVisual("Split", "🟨", "#fef3c7", "#b45309"),
}

_TYPE_FILTER_ORDER = [
    "service_markup",
    "dedup_tu",
    "normalize_spaces",
    "remove_garbage_segment",
    "split",
]

_STATUS_ACCEPTED_BG = QColor("#eaf8ef")
_STATUS_REJECTED_BG = QColor("#fdeaea")


class ReviewDialog(QDialog):
    """Modal dialog that lets the user approve proposed plan edits."""

    TYPE_FILTER_SETTINGS_KEY = "review/type_filters"

    def __init__(
        self,
        plans: PlanPhaseResult,
        parent: QWidget | None = None,
        export_callback: Callable[[PlanPhaseResult], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review proposed edits - approve before writing")
        self.resize(1240, 760)
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )

        self._plans = plans
        self._export_callback = export_callback
        self._settings = QSettings(APP_NAME, f"{APP_NAME}-gui")
        self._suppress_check_signal = False
        self._type_filter_sync = False
        self._type_filter_checkboxes: dict[str, QCheckBox] = {}
        self._type_filter_counts: dict[str, int] = {}
        self._status_buttons: dict[str, QPushButton] = {}
        self._shortcuts: list[QShortcut] = []

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["TU", "Тип", "Описание"])
        self._tree.setColumnWidth(0, 250)
        self._tree.setColumnWidth(1, 140)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setStyleSheet(
            """
            QTreeWidget::item:selected:active {
                background: #ffe08a;
                color: #111827;
                border: 1px solid #d97706;
            }
            QTreeWidget::item:selected:!active {
                background: #ffefb8;
                color: #111827;
            }
            QTreeWidget::item:hover {
                background: #e9eef5;
                color: #111827;
            }
            QTreeWidget::item:selected:hover {
                background: #ffe08a;
                color: #111827;
                border: 1px solid #d97706;
            }
            """
        )
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.setAlternatingRowColors(True)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Поиск: TU #, правило, текст...")
        self._search_input.textChanged.connect(self._apply_filter)

        self._status_group = QButtonGroup(self)
        self._status_group.setExclusive(True)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        status_row.addWidget(QLabel("Фильтр:"))
        for key, label in (
            ("all", "Все"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(self._apply_filter)
            self._status_group.addButton(button)
            self._status_buttons[key] = button
            status_row.addWidget(button)
        self._status_buttons["all"].setChecked(True)
        status_row.addStretch(1)
        status_row.addWidget(QLabel("Поиск TU:"))
        status_row.addWidget(self._search_input, 1)

        self._type_filter_all_checkbox = QCheckBox("Все типы")
        self._type_filter_all_checkbox.setTristate(True)
        self._type_filter_all_checkbox.stateChanged.connect(self._on_all_type_filter_changed)

        self._type_filter_rows_layout = QHBoxLayout()
        self._type_filter_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._type_filter_rows_layout.setSpacing(8)

        type_filter_row = QHBoxLayout()
        type_filter_row.setContentsMargins(0, 0, 0, 0)
        type_filter_row.setSpacing(8)
        type_filter_row.addWidget(QLabel("Типы:"))
        type_filter_row.addWidget(self._type_filter_all_checkbox)
        type_filter_row.addLayout(self._type_filter_rows_layout)
        type_filter_row.addStretch(1)

        btn_export_package = QPushButton("Экспортировать пакет")
        btn_export_package.clicked.connect(self._on_export_package)
        type_filter_row.addWidget(btn_export_package)

        filter_layout = QVBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(4)
        filter_layout.addLayout(status_row)
        filter_layout.addLayout(type_filter_row)

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(4)
        tree_layout.addLayout(filter_layout)
        tree_layout.addWidget(self._tree, 1)

        self._preview_meta = QLabel()
        self._preview_meta.setWordWrap(True)
        self._preview_meta.setText("Выберите правку слева для просмотра diff.")
        self._preview_src = QTextEdit()
        self._preview_src.setReadOnly(True)
        self._preview_src.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._preview_src.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self._preview_src.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._preview_src.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._preview_tgt = QTextEdit()
        self._preview_tgt.setReadOnly(True)
        self._preview_tgt.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._preview_tgt.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self._preview_tgt.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._preview_tgt.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono_font.setPointSize(max(9, mono_font.pointSize()))
        self._preview_src.setFont(mono_font)
        self._preview_tgt.setFont(mono_font)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(self._preview_meta)
        preview_layout.addWidget(QLabel("Source diff (- removed, + added)"))
        preview_layout.addWidget(self._preview_src, 1)
        preview_layout.addWidget(QLabel("Target diff (- removed, + added)"))
        preview_layout.addWidget(self._preview_tgt, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(tree_panel)
        splitter.addWidget(preview_panel)
        splitter.setSizes([560, 700])

        btn_accept_all = QPushButton("Принять все")
        btn_reject_all = QPushButton("Отклонить все")
        btn_accept_group = QPushButton("Принять группу")
        btn_reject_group = QPushButton("Отклонить группу")
        btn_accept_all.clicked.connect(lambda: self._bulk_set(True, scope="all"))
        btn_reject_all.clicked.connect(lambda: self._bulk_set(False, scope="all"))
        btn_accept_group.clicked.connect(lambda: self._bulk_set(True, scope="group"))
        btn_reject_group.clicked.connect(lambda: self._bulk_set(False, scope="group"))

        bulk_row = QHBoxLayout()
        bulk_row.addWidget(btn_accept_all)
        bulk_row.addWidget(btn_reject_all)
        bulk_row.addSpacing(8)
        bulk_row.addWidget(btn_accept_group)
        bulk_row.addWidget(btn_reject_group)
        bulk_row.addStretch(1)
        self._summary_label = QLabel()
        bulk_row.addWidget(self._summary_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if apply_button is not None:
            apply_button.setText("Применить правки")
            apply_button.setProperty("role", "primary")
            apply_button.clicked.connect(self.accept)
        if cancel_button is not None:
            cancel_button.setText("Отмена")
            cancel_button.clicked.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(splitter, 1)
        root.addLayout(bulk_row)
        root.addWidget(buttons)

        self._populate()
        self._rebuild_type_filters()
        self._refresh_status_button_labels()
        self._apply_filter()
        self._refresh_summary()
        self._bind_shortcuts()

    def _bind_shortcuts(self) -> None:
        for seq, delta in (("J", 1), ("K", -1)):
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.activated.connect(lambda d=delta: self._move_selection(d))
            self._shortcuts.append(shortcut)
        toggle_shortcut = QShortcut(QKeySequence("Space"), self)
        toggle_shortcut.activated.connect(self._toggle_current_proposal)
        self._shortcuts.append(toggle_shortcut)

    def _populate(self) -> None:
        self._suppress_check_signal = True
        try:
            for file_result in self._plans.files:
                file_item = self._build_file_item(file_result)
                self._tree.addTopLevelItem(file_item)
                file_item.setExpanded(True)
        finally:
            self._suppress_check_signal = False

        first = self._first_visible_proposal()
        if first is not None:
            self._tree.setCurrentItem(first)

    def _build_file_item(self, file_result: FilePlanResult) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, FILE_ROLE, file_result)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        item.setFirstColumnSpanned(True)
        self._style_file_item(item)

        grouped: dict[str, list[Proposal]] = {}
        for proposal in file_result.plan.proposals:
            type_key = self._proposal_filter_type(proposal)
            grouped.setdefault(type_key, []).append(proposal)

        for type_key in self._ordered_type_filter_keys(set(grouped)):
            group_item = self._build_group_item(type_key, grouped[type_key])
            item.addChild(group_item)
            if type_key == "service_markup":
                group_item.setExpanded(True)

        self._refresh_file_header(item)
        return item

    def _build_group_item(self, type_key: str, proposals: list[Proposal]) -> QTreeWidgetItem:
        group_item = QTreeWidgetItem()
        group_item.setData(0, GROUP_ROLE, True)
        group_item.setData(0, TYPE_ROLE, type_key)
        group_item.setFlags(
            group_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
        )
        for proposal in proposals:
            group_item.addChild(self._build_proposal_item(proposal))
        self._refresh_group_header(group_item)
        return group_item

    def _build_proposal_item(self, proposal: Proposal) -> QTreeWidgetItem:
        type_key = self._proposal_filter_type(proposal)
        visual = self._type_visual(type_key)
        item = QTreeWidgetItem(
            [
                f"TU #{proposal.tu_index + 1}",
                f"{visual.badge} {visual.label}",
                self._proposal_short_description(proposal),
            ]
        )
        item.setData(0, PROPOSAL_ROLE, proposal)
        item.setData(0, TYPE_ROLE, type_key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if proposal.accepted else Qt.CheckState.Unchecked)
        item.setToolTip(0, self._proposal_detail_text(proposal, visual.label))
        item.setToolTip(1, self._proposal_detail_text(proposal, visual.label))
        item.setToolTip(2, self._proposal_detail_text(proposal, visual.label))
        item.setBackground(1, QColor(visual.bg))
        self._apply_proposal_status_style(item, proposal.accepted)
        return item

    def _proposal_short_description(self, proposal: Proposal) -> str:
        if proposal.kind == "split":
            base = f"Split into {len(proposal.src_parts)} parts"
        else:
            base = proposal.message.strip() or proposal.rule.strip() or "Cleanup edit"
        one_line = " ".join(base.split())
        return one_line[:117] + "..." if len(one_line) > 120 else one_line

    def _proposal_detail_text(self, proposal: Proposal, type_label: str) -> str:
        lines = [
            f"ID: {proposal.proposal_id}",
            f"Type: {type_label}",
            f"TU: #{proposal.tu_index + 1}",
        ]
        if proposal.kind == "cleanup":
            lines.append(f"Rule: {proposal.rule or 'cleanup'}")
            if proposal.message:
                lines.append(f"Message: {proposal.message}")
        if proposal.confidence:
            lines.append(f"Confidence: {proposal.confidence}")
        return "\n".join(lines)

    def _proposal_filter_type(self, proposal: Proposal) -> str:
        if proposal.kind == "split":
            return "split"
        rule = proposal.rule or "cleanup"
        if rule in _SERVICE_MARKUP_RULES:
            return "service_markup"
        return rule

    def _type_visual(self, type_key: str) -> _TypeVisual:
        return _TYPE_VISUALS.get(type_key, _TypeVisual(type_key, "⬜", "#e5e7eb", "#374151"))

    def _ordered_type_filter_keys(self, keys: set[str]) -> list[str]:
        ordered = [key for key in _TYPE_FILTER_ORDER if key in keys]
        ordered.extend(sorted(key for key in keys if key not in _TYPE_FILTER_ORDER))
        return ordered

    def _collect_type_filter_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for file_result in self._plans.files:
            for proposal in file_result.plan.proposals:
                type_key = self._proposal_filter_type(proposal)
                counts[type_key] = counts.get(type_key, 0) + 1
        return counts

    def _clear_type_filter_rows(self) -> None:
        while self._type_filter_rows_layout.count():
            item = self._type_filter_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _load_saved_type_filter_keys(self, available: set[str]) -> set[str]:
        raw = self._settings.value(self.TYPE_FILTER_SETTINGS_KEY, [])
        values: list[str]
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple)):
            values = [str(item) for item in raw]
        else:
            values = []
        selected = {value for value in values if value in available}
        return selected or set(available)

    def _save_type_filter_keys(self) -> None:
        self._settings.setValue(
            self.TYPE_FILTER_SETTINGS_KEY,
            sorted(self._selected_type_filter_keys()),
        )

    def _selected_type_filter_keys(self) -> set[str]:
        return {
            key
            for key, checkbox in self._type_filter_checkboxes.items()
            if checkbox.isChecked()
        }

    def _set_type_filter_checked_keys(self, keys: set[str], save: bool = True) -> None:
        self._type_filter_sync = True
        try:
            for key, checkbox in self._type_filter_checkboxes.items():
                checkbox.setChecked(key in keys)
        finally:
            self._type_filter_sync = False
        self._sync_all_type_filter_checkbox()
        if save:
            self._save_type_filter_keys()
        self._apply_filter()

    def _sync_all_type_filter_checkbox(self) -> None:
        if not self._type_filter_checkboxes:
            self._type_filter_all_checkbox.setCheckState(Qt.CheckState.Unchecked)
            return
        checked_count = sum(1 for checkbox in self._type_filter_checkboxes.values() if checkbox.isChecked())
        total = len(self._type_filter_checkboxes)
        self._type_filter_sync = True
        try:
            if checked_count == 0:
                self._type_filter_all_checkbox.setCheckState(Qt.CheckState.Unchecked)
            elif checked_count == total:
                self._type_filter_all_checkbox.setCheckState(Qt.CheckState.Checked)
            else:
                self._type_filter_all_checkbox.setCheckState(Qt.CheckState.PartiallyChecked)
        finally:
            self._type_filter_sync = False

    def _on_all_type_filter_changed(self, state: int) -> None:
        if self._type_filter_sync:
            return
        if state == int(Qt.CheckState.PartiallyChecked):
            return
        should_check = state == int(Qt.CheckState.Checked)
        self._type_filter_sync = True
        try:
            for checkbox in self._type_filter_checkboxes.values():
                checkbox.setChecked(should_check)
        finally:
            self._type_filter_sync = False
        self._sync_all_type_filter_checkbox()
        self._save_type_filter_keys()
        self._apply_filter()

    def _on_type_filter_changed(self, _state: int) -> None:
        if self._type_filter_sync:
            return
        self._sync_all_type_filter_checkbox()
        self._save_type_filter_keys()
        self._apply_filter()

    def _rebuild_type_filters(self) -> None:
        self._type_filter_counts = self._collect_type_filter_counts()
        available = set(self._type_filter_counts)
        self._type_filter_checkboxes = {}
        self._clear_type_filter_rows()
        for key in self._ordered_type_filter_keys(available):
            visual = self._type_visual(key)
            checkbox = QCheckBox(f"{visual.badge} {visual.label} {self._type_filter_counts[key]}")
            checkbox.stateChanged.connect(self._on_type_filter_changed)
            self._type_filter_rows_layout.addWidget(checkbox)
            self._type_filter_checkboxes[key] = checkbox
        selected = self._load_saved_type_filter_keys(available)
        self._set_type_filter_checked_keys(selected, save=False)

    def _on_export_package(self) -> None:
        if self._export_callback is not None:
            self._export_callback(self._plans)

    def _refresh_status_button_labels(self) -> None:
        total = sum(len(file.plan.proposals) for file in self._plans.files)
        accepted = sum(1 for file in self._plans.files for proposal in file.plan.proposals if proposal.accepted)
        rejected = total - accepted
        self._status_buttons["all"].setText(f"Все ({total})")
        self._status_buttons["accepted"].setText(f"Accepted ({accepted})")
        self._status_buttons["rejected"].setText(f"Rejected ({rejected})")

    def _active_status_filter(self) -> str:
        for key, button in self._status_buttons.items():
            if button.isChecked():
                return key
        return "all"

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suppress_check_signal or column != 0:
            return

        proposal = item.data(0, PROPOSAL_ROLE)
        self._suppress_check_signal = True
        try:
            if isinstance(proposal, Proposal):
                accepted = item.checkState(0) == Qt.CheckState.Checked
                proposal.accepted = accepted
                self._apply_proposal_status_style(item, accepted)
            else:
                for proposal_item in self._collect_branch_proposals(item):
                    branch_proposal = proposal_item.data(0, PROPOSAL_ROLE)
                    if not isinstance(branch_proposal, Proposal):
                        continue
                    accepted = proposal_item.checkState(0) == Qt.CheckState.Checked
                    branch_proposal.accepted = accepted
                    self._apply_proposal_status_style(proposal_item, accepted)
        finally:
            self._suppress_check_signal = False

        file_item = self._resolve_file_item(item)
        if file_item is not None:
            self._refresh_file_header(file_item)
            for group_index in range(file_item.childCount()):
                group_item = file_item.child(group_index)
                self._refresh_group_header(group_item)
        self._refresh_status_button_labels()
        self._refresh_summary()
        self._apply_filter()

    def _apply_proposal_status_style(self, item: QTreeWidgetItem, accepted: bool) -> None:
        state_bg = _STATUS_ACCEPTED_BG if accepted else _STATUS_REJECTED_BG
        item.setBackground(0, state_bg)

    def _style_file_item(self, file_item: QTreeWidgetItem) -> None:
        font: QFont = file_item.font(0)
        font.setBold(True)
        for col in range(self._tree.columnCount()):
            file_item.setFont(col, font)
            file_item.setBackground(col, QColor("#e5edf4"))
            file_item.setForeground(col, QColor("#0f172a"))

    def _refresh_file_header(self, file_item: QTreeWidgetItem | None) -> None:
        if file_item is None:
            return
        file_result = file_item.data(0, FILE_ROLE)
        if not isinstance(file_result, FilePlanResult):
            return
        accepted = sum(1 for p in file_result.plan.proposals if p.accepted)
        total = len(file_result.plan.proposals)
        file_item.setText(0, f"{Path(file_result.input_path).name} ({accepted}/{total} accepted)")
        file_item.setText(1, "")
        file_item.setText(2, f"{file_result.plan.total_tus} TU")

    def _refresh_group_header(self, group_item: QTreeWidgetItem | None) -> None:
        if group_item is None:
            return
        type_key = str(group_item.data(0, TYPE_ROLE) or "")
        visual = self._type_visual(type_key)
        total = group_item.childCount()
        accepted = 0
        for idx in range(group_item.childCount()):
            child = group_item.child(idx)
            if child.checkState(0) == Qt.CheckState.Checked:
                accepted += 1
        group_item.setText(0, f"{visual.badge} {visual.label} ({accepted}/{total})")
        group_item.setText(1, "")
        group_item.setText(2, "Группа правок")
        group_item.setToolTip(0, f"{visual.label}: {total} правок")
        for col in range(self._tree.columnCount()):
            group_item.setBackground(col, QColor("#f3f6f9"))
            group_item.setForeground(col, QColor("#334155"))

    def _on_current_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        proposal = current.data(0, PROPOSAL_ROLE)
        if isinstance(proposal, Proposal):
            self._render_preview(proposal)
        else:
            self._preview_meta.setText("Выберите правку слева для просмотра diff.")
            self._preview_src.clear()
            self._preview_tgt.clear()

    def _render_preview(self, proposal: Proposal) -> None:
        type_label = self._type_visual(self._proposal_filter_type(proposal)).label
        if proposal.kind == "split":
            before_src = proposal.original_src
            after_src = "\n".join(proposal.src_parts)
            before_tgt = proposal.original_tgt
            after_tgt = "\n".join(proposal.tgt_parts)
        else:
            before_src = proposal.before_src or proposal.original_src
            after_src = proposal.after_src
            before_tgt = proposal.before_tgt or proposal.original_tgt
            after_tgt = proposal.after_tgt
        self._preview_meta.setText(
            f"TU #{proposal.tu_index + 1} • {type_label}\n"
            f"Rule: {proposal.rule or '-'}"
        )
        _write_git_diff(self._preview_src, before_src, after_src)
        _write_git_diff(self._preview_tgt, before_tgt, after_tgt)

    def _refresh_summary(self) -> None:
        total = sum(len(file.plan.proposals) for file in self._plans.files)
        accepted = sum(1 for file in self._plans.files for proposal in file.plan.proposals if proposal.accepted)
        rejected = total - accepted
        self._summary_label.setText(f"Accepted: {accepted}/{total} • Rejected: {rejected}")

    def _resolve_file_item(self, item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        current = item
        while current is not None:
            if isinstance(current.data(0, FILE_ROLE), FilePlanResult):
                return current
            current = current.parent()
        return None

    def _resolve_group_item(self, item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        current = item
        while current is not None:
            if bool(current.data(0, GROUP_ROLE)):
                return current
            current = current.parent()
        return None

    def _collect_branch_proposals(self, branch: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        result: list[QTreeWidgetItem] = []
        stack = [branch]
        while stack:
            node = stack.pop()
            if isinstance(node.data(0, PROPOSAL_ROLE), Proposal):
                result.append(node)
                continue
            for idx in range(node.childCount()):
                stack.append(node.child(idx))
        return result

    def _bulk_set(self, accepted: bool, scope: str) -> None:
        target_items: list[QTreeWidgetItem] = []
        if scope == "all":
            target_items = [self._tree.topLevelItem(index) for index in range(self._tree.topLevelItemCount())]
        elif scope == "group":
            group_item = self._resolve_group_item(self._tree.currentItem())
            if group_item is not None:
                target_items = [group_item]

        self._suppress_check_signal = True
        try:
            check_state = Qt.CheckState.Checked if accepted else Qt.CheckState.Unchecked
            for branch in target_items:
                if branch is None:
                    continue
                branch.setCheckState(0, check_state)
                for proposal_item in self._collect_branch_proposals(branch):
                    proposal = proposal_item.data(0, PROPOSAL_ROLE)
                    if not isinstance(proposal, Proposal):
                        continue
                    proposal.accepted = accepted
                    proposal_item.setCheckState(0, check_state)
                    self._apply_proposal_status_style(proposal_item, accepted)
                file_item = self._resolve_file_item(branch)
                if file_item is not None:
                    self._refresh_file_header(file_item)
                    for group_index in range(file_item.childCount()):
                        self._refresh_group_header(file_item.child(group_index))
        finally:
            self._suppress_check_signal = False

        self._refresh_status_button_labels()
        self._refresh_summary()
        self._apply_filter()

    def _move_selection(self, delta: int) -> None:
        if isinstance(self.focusWidget(), QLineEdit):
            return
        visible_items = self._visible_proposal_items()
        if not visible_items:
            return
        current = self._tree.currentItem()
        if current not in visible_items:
            target = visible_items[0]
        else:
            index = visible_items.index(current)
            target = visible_items[(index + delta) % len(visible_items)]
        self._tree.setCurrentItem(target)
        self._tree.scrollToItem(target)

    def _toggle_current_proposal(self) -> None:
        if isinstance(self.focusWidget(), QLineEdit):
            return
        current = self._tree.currentItem()
        if current is None:
            return
        proposal = current.data(0, PROPOSAL_ROLE)
        if not isinstance(proposal, Proposal):
            return
        new_state = (
            Qt.CheckState.Unchecked
            if current.checkState(0) == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        current.setCheckState(0, new_state)

    def _visible_proposal_items(self) -> list[QTreeWidgetItem]:
        result: list[QTreeWidgetItem] = []
        for file_index in range(self._tree.topLevelItemCount()):
            file_item = self._tree.topLevelItem(file_index)
            if file_item is None or file_item.isHidden():
                continue
            for group_index in range(file_item.childCount()):
                group_item = file_item.child(group_index)
                if group_item is None or group_item.isHidden():
                    continue
                for proposal_index in range(group_item.childCount()):
                    proposal_item = group_item.child(proposal_index)
                    if proposal_item is not None and not proposal_item.isHidden():
                        result.append(proposal_item)
        return result

    def _first_visible_proposal(self) -> QTreeWidgetItem | None:
        proposals = self._visible_proposal_items()
        return proposals[0] if proposals else None

    def _matches_search(self, proposal: Proposal, query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(
            [
                f"tu #{proposal.tu_index + 1}",
                proposal.proposal_id,
                proposal.rule,
                proposal.message,
                proposal.before_src,
                proposal.after_src,
                proposal.before_tgt,
                proposal.after_tgt,
            ]
        ).lower()
        return query in haystack

    def _apply_filter(self) -> None:
        selected_type_keys = self._selected_type_filter_keys()
        status_filter = self._active_status_filter()
        query = self._search_input.text().strip().lower()

        for file_index in range(self._tree.topLevelItemCount()):
            file_item = self._tree.topLevelItem(file_index)
            if file_item is None:
                continue
            visible_groups = 0
            for group_index in range(file_item.childCount()):
                group_item = file_item.child(group_index)
                if group_item is None:
                    continue
                group_type = str(group_item.data(0, TYPE_ROLE) or "")
                type_allowed = group_type in selected_type_keys
                visible_items = 0
                for proposal_index in range(group_item.childCount()):
                    proposal_item = group_item.child(proposal_index)
                    proposal = proposal_item.data(0, PROPOSAL_ROLE)
                    if not isinstance(proposal, Proposal):
                        continue
                    show = type_allowed
                    if show and status_filter == "accepted" and not proposal.accepted:
                        show = False
                    elif show and status_filter == "rejected" and proposal.accepted:
                        show = False
                    if show and not self._matches_search(proposal, query):
                        show = False
                    proposal_item.setHidden(not show)
                    if show:
                        visible_items += 1
                group_item.setHidden(visible_items == 0)
                if visible_items > 0:
                    visible_groups += 1
            file_item.setHidden(visible_groups == 0)

        current = self._tree.currentItem()
        if current is None or current.isHidden():
            first = self._first_visible_proposal()
            if first is not None:
                self._tree.setCurrentItem(first)


_DEL_FMT = QTextCharFormat()
_DEL_FMT.setBackground(QColor("#fee2e2"))
_DEL_FMT.setForeground(QColor("#991b1b"))
_DEL_FMT.setFontStrikeOut(True)

_INS_FMT = QTextCharFormat()
_INS_FMT.setBackground(QColor("#dcfce7"))
_INS_FMT.setForeground(QColor("#166534"))

_EQ_FMT = QTextCharFormat()
_EQ_FMT.setForeground(QColor("#334155"))


def _split_for_diff(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines()


def _insert_diff_lines(cursor: QTextCursor, lines: list[str], prefix: str, fmt: QTextCharFormat) -> None:
    if not lines:
        return
    for line in lines:
        cursor.insertText(f"{prefix} {line}\n", fmt)


def _tokenize_for_inline_diff(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"\w+|\s+|[^\w\s]", text, flags=re.UNICODE)


def _inline_diff_segments(
    before_line: str,
    after_line: str,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    before_tokens = _tokenize_for_inline_diff(before_line)
    after_tokens = _tokenize_for_inline_diff(after_line)
    matcher = SequenceMatcher(a=before_tokens, b=after_tokens, autojunk=False)
    before_segments: list[tuple[str, str]] = []
    after_segments: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = "".join(before_tokens[i1:i2])
            if text:
                before_segments.append((text, "equal"))
                after_segments.append((text, "equal"))
        elif tag == "delete":
            text = "".join(before_tokens[i1:i2])
            if text:
                before_segments.append((text, "delete"))
        elif tag == "insert":
            text = "".join(after_tokens[j1:j2])
            if text:
                after_segments.append((text, "insert"))
        elif tag == "replace":
            before_text = "".join(before_tokens[i1:i2])
            after_text = "".join(after_tokens[j1:j2])
            if before_text:
                before_segments.append((before_text, "delete"))
            if after_text:
                after_segments.append((after_text, "insert"))
    return before_segments, after_segments


def _insert_inline_segment_line(
    cursor: QTextCursor,
    *,
    prefix: str,
    segments: list[tuple[str, str]],
) -> None:
    prefix_fmt = _DEL_FMT if prefix == "-" else _INS_FMT
    cursor.insertText(f"{prefix} ", prefix_fmt)
    for text, kind in segments:
        if kind == "equal":
            cursor.insertText(text, _EQ_FMT)
        elif kind == "delete":
            cursor.insertText(text, _DEL_FMT)
        else:
            cursor.insertText(text, _INS_FMT)
    cursor.insertText("\n", _EQ_FMT)


def _insert_inline_replace_pair(cursor: QTextCursor, before_line: str, after_line: str) -> None:
    before_segments, after_segments = _inline_diff_segments(before_line, after_line)
    _insert_inline_segment_line(cursor, prefix="-", segments=before_segments)
    _insert_inline_segment_line(cursor, prefix="+", segments=after_segments)


def _write_git_diff(widget: QTextEdit, before: str, after: str) -> None:
    widget.clear()
    before_lines = _split_for_diff(before)
    after_lines = _split_for_diff(after)
    if not before_lines and not after_lines:
        widget.setPlainText("  <empty>")
        return

    cursor = widget.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    matcher = SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            _insert_diff_lines(cursor, before_lines[i1:i2], " ", _EQ_FMT)
        elif tag == "delete":
            _insert_diff_lines(cursor, before_lines[i1:i2], "-", _DEL_FMT)
        elif tag == "insert":
            _insert_diff_lines(cursor, after_lines[j1:j2], "+", _INS_FMT)
        elif tag == "replace":
            before_chunk = before_lines[i1:i2]
            after_chunk = after_lines[j1:j2]
            paired = min(len(before_chunk), len(after_chunk))
            for idx in range(paired):
                _insert_inline_replace_pair(cursor, before_chunk[idx], after_chunk[idx])
            if len(before_chunk) > paired:
                _insert_diff_lines(cursor, before_chunk[paired:], "-", _DEL_FMT)
            if len(after_chunk) > paired:
                _insert_diff_lines(cursor, after_chunk[paired:], "+", _INS_FMT)
