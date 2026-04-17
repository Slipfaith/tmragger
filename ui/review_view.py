"""Plan review UI — user approves/rejects proposed edits before apply.

The worker runs in two phases: plan (no writes, produces a
``PlanPhaseResult``) and apply. This dialog sits between them. The user
sees every file's proposed edits as a checkbox tree, inspects a
before→after preview of whichever row they select, and then either
presses Apply (which mutates ``Proposal.accepted`` in place and returns
``QDialog.Accepted``) or Cancel (``Rejected``).

We use ``QTreeWidget`` (not a custom model) on purpose — plans are
small (typically dozens to a few hundred rows) and a model/view impl
would be 5× the code for no practical benefit here.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
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


# Role used to stash the Proposal reference on leaf items so toggling
# the check state can find the underlying object in O(1).
PROPOSAL_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_ROLE = Qt.ItemDataRole.UserRole + 2


class ReviewDialog(QDialog):
    """Modal dialog that lets the user accept/reject plan proposals."""

    def __init__(self, plans: PlanPhaseResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review proposed edits — approve before writing")
        self.resize(1100, 720)
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )

        self._plans = plans
        self._suppress_check_signal = False

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["TU / правка", "Тип", "Conf", "Правило / сообщение"])
        self._tree.setColumnWidth(0, 360)
        self._tree.setColumnWidth(1, 70)
        self._tree.setColumnWidth(2, 60)
        self._tree.setUniformRowHeights(True)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.currentItemChanged.connect(self._on_current_changed)

        # Filter bar
        self._filter_type = QComboBox()
        self._filter_type.addItems(["Все типы", "split", "cleanup"])
        self._filter_conf = QComboBox()
        self._filter_conf.addItems(["Все уровни", "HIGH", "MEDIUM", "LOW"])
        self._filter_status = QComboBox()
        self._filter_status.addItems(["Все статусы", "Принятые", "Отклонённые"])
        for combo in (self._filter_type, self._filter_conf, self._filter_status):
            combo.currentIndexChanged.connect(self._apply_filter)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Фильтр:"))
        filter_row.addWidget(self._filter_type)
        filter_row.addWidget(self._filter_conf)
        filter_row.addWidget(self._filter_status)
        filter_row.addStretch(1)

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(4)
        tree_layout.addLayout(filter_row)
        tree_layout.addWidget(self._tree, 1)

        self._preview_src = QTextEdit()
        self._preview_src.setReadOnly(True)
        self._preview_tgt = QTextEdit()
        self._preview_tgt.setReadOnly(True)
        self._preview_src.setPlaceholderText("Выберите строку в дереве слева")
        self._preview_tgt.setPlaceholderText("")

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.addWidget(QLabel("Source (до → после)"))
        preview_layout.addWidget(self._preview_src, 1)
        preview_layout.addWidget(QLabel("Target (до → после)"))
        preview_layout.addWidget(self._preview_tgt, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(tree_panel)
        splitter.addWidget(preview_panel)
        splitter.setSizes([600, 500])

        # Bulk-action buttons.
        btn_accept_all = QPushButton("Принять все")
        btn_reject_all = QPushButton("Отклонить все")
        btn_accept_file = QPushButton("Принять текущий файл")
        btn_reject_file = QPushButton("Отклонить текущий файл")
        btn_accept_all.clicked.connect(lambda: self._bulk_set(True, scope="all"))
        btn_reject_all.clicked.connect(lambda: self._bulk_set(False, scope="all"))
        btn_accept_file.clicked.connect(lambda: self._bulk_set(True, scope="file"))
        btn_reject_file.clicked.connect(lambda: self._bulk_set(False, scope="file"))

        bulk_row = QHBoxLayout()
        bulk_row.addWidget(btn_accept_all)
        bulk_row.addWidget(btn_reject_all)
        bulk_row.addSpacing(12)
        bulk_row.addWidget(btn_accept_file)
        bulk_row.addWidget(btn_reject_file)
        bulk_row.addStretch(1)
        self._summary_label = QLabel()
        bulk_row.addWidget(self._summary_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("Применить правки")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(
                "Снимите галочки с тех правок, которые применять не надо. "
                "После нажатия «Применить» пакет будет записан на диск."
            )
        )
        root.addWidget(splitter, 1)
        root.addLayout(bulk_row)
        root.addWidget(buttons)

        self._populate()
        self._refresh_summary()

    # ----------------------------------------------------------- population
    def _populate(self) -> None:
        self._suppress_check_signal = True
        try:
            for file_result in self._plans.files:
                file_item = self._build_file_item(file_result)
                self._tree.addTopLevelItem(file_item)
                file_item.setExpanded(True)
        finally:
            self._suppress_check_signal = False

        if self._tree.topLevelItemCount() > 0:
            first = self._tree.topLevelItem(0)
            if first and first.childCount() > 0:
                self._tree.setCurrentItem(first.child(0))

    def _build_file_item(self, file_result: FilePlanResult) -> QTreeWidgetItem:
        accepted = sum(1 for p in file_result.plan.proposals if p.accepted)
        total = len(file_result.plan.proposals)
        item = QTreeWidgetItem(
            [
                f"{Path(file_result.input_path).name}  ({accepted}/{total} accepted)",
                "",
                "",
                f"{file_result.plan.total_tus} TU",
            ]
        )
        item.setData(0, FILE_ROLE, file_result)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Checked if accepted == total and total > 0 else
                            (Qt.CheckState.Unchecked if accepted == 0 else Qt.CheckState.PartiallyChecked))
        for proposal in file_result.plan.proposals:
            item.addChild(self._build_proposal_item(proposal))
        return item

    def _build_proposal_item(self, proposal: Proposal) -> QTreeWidgetItem:
        if proposal.kind == "split":
            title = f"TU #{proposal.tu_index + 1} — split into {len(proposal.src_parts)} parts"
            detail = ""
        else:
            title = f"TU #{proposal.tu_index + 1} — cleanup ({proposal.rule})"
            detail = proposal.message
        item = QTreeWidgetItem([title, proposal.kind, proposal.confidence, detail])
        item.setData(0, PROPOSAL_ROLE, proposal)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            0,
            Qt.CheckState.Checked if proposal.accepted else Qt.CheckState.Unchecked,
        )
        return item

    # ------------------------------------------------------------- handlers
    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suppress_check_signal or column != 0:
            return
        proposal = item.data(0, PROPOSAL_ROLE)
        if isinstance(proposal, Proposal):
            proposal.accepted = item.checkState(0) == Qt.CheckState.Checked
            self._refresh_file_header(item.parent())
        self._refresh_summary()
        if self._filter_status.currentIndex() != 0:
            self._apply_filter()

    def _refresh_file_header(self, file_item: QTreeWidgetItem | None) -> None:
        if file_item is None:
            return
        file_result = file_item.data(0, FILE_ROLE)
        if not isinstance(file_result, FilePlanResult):
            return
        accepted = sum(1 for p in file_result.plan.proposals if p.accepted)
        total = len(file_result.plan.proposals)
        file_item.setText(
            0, f"{Path(file_result.input_path).name}  ({accepted}/{total} accepted)"
        )

    def _on_current_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        if current is None:
            return
        proposal = current.data(0, PROPOSAL_ROLE)
        if isinstance(proposal, Proposal):
            self._render_preview(proposal)
        else:
            self._preview_src.clear()
            self._preview_tgt.clear()

    # ------------------------------------------------------------ rendering
    def _render_preview(self, proposal: Proposal) -> None:
        if proposal.kind == "split":
            before_src = proposal.original_src
            after_src = "\n---\n".join(proposal.src_parts)
            before_tgt = proposal.original_tgt
            after_tgt = "\n---\n".join(proposal.tgt_parts)
        else:
            before_src = proposal.before_src or proposal.original_src
            after_src = proposal.after_src
            before_tgt = proposal.before_tgt or proposal.original_tgt
            after_tgt = proposal.after_tgt
        _write_diff(self._preview_src, before_src, after_src)
        _write_diff(self._preview_tgt, before_tgt, after_tgt)

    def _refresh_summary(self) -> None:
        total = sum(len(f.plan.proposals) for f in self._plans.files)
        accepted = sum(
            1 for f in self._plans.files for p in f.plan.proposals if p.accepted
        )
        self._summary_label.setText(
            f"Принято: {accepted} / {total}"
        )

    # --------------------------------------------------------------- bulk
    def _bulk_set(self, accepted: bool, scope: str) -> None:
        target_items: list[QTreeWidgetItem] = []
        if scope == "all":
            target_items = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        elif scope == "file":
            current = self._tree.currentItem()
            while current is not None and current.parent() is not None:
                current = current.parent()
            if current is not None:
                target_items = [current]

        self._suppress_check_signal = True
        try:
            check_state = Qt.CheckState.Checked if accepted else Qt.CheckState.Unchecked
            for file_item in target_items:
                if file_item is None:
                    continue
                file_item.setCheckState(0, check_state)
                for j in range(file_item.childCount()):
                    child = file_item.child(j)
                    proposal = child.data(0, PROPOSAL_ROLE)
                    if isinstance(proposal, Proposal):
                        proposal.accepted = accepted
                    child.setCheckState(0, check_state)
                self._refresh_file_header(file_item)
        finally:
            self._suppress_check_signal = False
        self._refresh_summary()
        if self._filter_status.currentIndex() != 0:
            self._apply_filter()

    # --------------------------------------------------------------- filter
    def _apply_filter(self) -> None:
        type_filter = self._filter_type.currentText()
        conf_filter = self._filter_conf.currentText()
        status_filter = self._filter_status.currentText()

        for i in range(self._tree.topLevelItemCount()):
            file_item = self._tree.topLevelItem(i)
            if file_item is None:
                continue
            visible = 0
            for j in range(file_item.childCount()):
                child = file_item.child(j)
                proposal = child.data(0, PROPOSAL_ROLE)
                if not isinstance(proposal, Proposal):
                    child.setHidden(False)
                    continue
                show = True
                if type_filter not in ("Все типы",) and proposal.kind != type_filter:
                    show = False
                if conf_filter not in ("Все уровни",) and proposal.confidence != conf_filter:
                    show = False
                if status_filter == "Принятые" and not proposal.accepted:
                    show = False
                elif status_filter == "Отклонённые" and proposal.accepted:
                    show = False
                child.setHidden(not show)
                if show:
                    visible += 1
            file_item.setHidden(visible == 0)


# ------------------------------------------------------------- diff helpers


_DEL_FMT = QTextCharFormat()
_DEL_FMT.setBackground(QColor("#fde2e2"))
_DEL_FMT.setForeground(QColor("#8a1f1f"))

_INS_FMT = QTextCharFormat()
_INS_FMT.setBackground(QColor("#d8f5dd"))
_INS_FMT.setForeground(QColor("#1f6a32"))

_EQ_FMT = QTextCharFormat()
_EQ_FMT.setForeground(QColor("#0f172a"))

_HEADER_FMT = QTextCharFormat()
_HEADER_FMT.setForeground(QColor("#64748b"))


def _write_diff(widget: QTextEdit, before: str, after: str) -> None:
    """Write a compact inline diff (before above, after below) into widget."""
    widget.clear()
    cursor = widget.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)

    cursor.insertText("— до —\n", _HEADER_FMT)
    _insert_segments(cursor, before, after, show_side="before")
    cursor.insertText("\n\n— после —\n", _HEADER_FMT)
    _insert_segments(cursor, before, after, show_side="after")


def _insert_segments(cursor: QTextCursor, before: str, after: str, show_side: str) -> None:
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if show_side == "before":
            piece_eq = before[i1:i2]
            piece_chg = before[i1:i2]
        else:
            piece_eq = after[j1:j2]
            piece_chg = after[j1:j2]

        if tag == "equal":
            cursor.insertText(piece_eq, _EQ_FMT)
        elif tag == "delete":
            if show_side == "before":
                cursor.insertText(piece_chg, _DEL_FMT)
            # omit on "after"
        elif tag == "insert":
            if show_side == "after":
                cursor.insertText(piece_chg, _INS_FMT)
            # omit on "before"
        elif tag == "replace":
            if show_side == "before":
                cursor.insertText(before[i1:i2], _DEL_FMT)
            else:
                cursor.insertText(after[j1:j2], _INS_FMT)
