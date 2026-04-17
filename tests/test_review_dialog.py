"""Stage 2.3 tests: ReviewDialog proposal toggling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Qt on headless CI — use the offscreen platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

from core.plan import Proposal, RepairPlan, make_split_proposal_id
from core.repair import RepairStats
from ui.review_view import ReviewDialog, PROPOSAL_ROLE
from ui.types import FilePlanResult, PlanPhaseResult


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _make_plans() -> PlanPhaseResult:
    proposals = [
        Proposal(
            proposal_id=make_split_proposal_id(0),
            kind="split",
            tu_index=0,
            confidence="HIGH",
            src_parts=["Hello world.", "Next sentence!"],
            tgt_parts=["Privet mir.", "Sleduiushchee predlozhenie!"],
            original_src="Hello world. Next sentence!",
            original_tgt="Privet mir. Sleduiushchee predlozhenie!",
        ),
        Proposal(
            proposal_id=make_split_proposal_id(1),
            kind="split",
            tu_index=1,
            confidence="HIGH",
            src_parts=["Alpha one.", "Beta two."],
            tgt_parts=["Alfa raz.", "Beta dva."],
            original_src="Alpha one. Beta two.",
            original_tgt="Alfa raz. Beta dva.",
        ),
    ]
    plan = RepairPlan(input_path="x.tmx", total_tus=2, proposals=proposals)
    file = FilePlanResult(
        input_path=Path("x.tmx"),
        output_path=Path("x_repaired.tmx"),
        report_path=None,
        html_report_path=Path("x.html"),
        xlsx_report_path=Path("x.xlsx"),
        stats=RepairStats(
            total_tus=2, split_tus=0, created_tus=2,
            src_lang="en-US", tgt_lang="ru-RU", skipped_tus=0,
        ),
        plan=plan,
    )
    return PlanPhaseResult(files=[file])


def _iter_proposal_items(dialog: ReviewDialog):
    tree = dialog._tree
    for i in range(tree.topLevelItemCount()):
        parent = tree.topLevelItem(i)
        for j in range(parent.childCount()):
            yield parent.child(j)


def test_dialog_shows_every_proposal_as_checkable(qapp):
    plans = _make_plans()
    dialog = ReviewDialog(plans)
    items = list(_iter_proposal_items(dialog))
    assert len(items) == 2
    for item in items:
        assert item.flags() & Qt.ItemFlag.ItemIsUserCheckable
        assert item.checkState(0) == Qt.CheckState.Checked
        assert isinstance(item.data(0, PROPOSAL_ROLE), Proposal)


def test_unchecking_item_flips_proposal_accepted(qapp):
    plans = _make_plans()
    dialog = ReviewDialog(plans)
    items = list(_iter_proposal_items(dialog))

    items[0].setCheckState(0, Qt.CheckState.Unchecked)
    proposal = items[0].data(0, PROPOSAL_ROLE)
    assert proposal.accepted is False
    # Second stays accepted.
    assert items[1].data(0, PROPOSAL_ROLE).accepted is True

    accepted_ids = plans.files[0].plan.accepted_split_ids()
    assert accepted_ids == {make_split_proposal_id(1)}


def test_reject_all_bulk_button(qapp):
    plans = _make_plans()
    dialog = ReviewDialog(plans)
    dialog._bulk_set(False, scope="all")
    for item in _iter_proposal_items(dialog):
        assert item.checkState(0) == Qt.CheckState.Unchecked
        assert item.data(0, PROPOSAL_ROLE).accepted is False
    assert plans.files[0].plan.accepted_split_ids() == set()
