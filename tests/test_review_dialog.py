"""Stage 2.3 tests: ReviewDialog proposal toggling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Qt on headless CI — use the offscreen platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.plan import (
    Proposal,
    RepairPlan,
    make_cleanup_proposal_id,
    make_split_proposal_id,
)
from core.repair import RepairStats
from ui.review_view import (
    GROUP_ROLE,
    PROPOSAL_ROLE,
    TYPE_ROLE,
    ReviewDialog,
    _inline_diff_segments,
)
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


def _make_mixed_plans() -> PlanPhaseResult:
    proposals = [
        Proposal(
            proposal_id=make_split_proposal_id(0),
            kind="split",
            tu_index=0,
            confidence="HIGH",
            src_parts=["Split one.", "Split two."],
            tgt_parts=["Razdelenie odin.", "Razdelenie dva."],
            original_src="Split one. Split two.",
            original_tgt="Razdelenie odin. Razdelenie dva.",
        ),
        Proposal(
            proposal_id=make_cleanup_proposal_id(1, "normalize_spaces", 0),
            kind="cleanup",
            tu_index=1,
            rule="normalize_spaces",
            message="Space normalization",
            before_src="Hello   world",
            after_src="Hello world",
            before_tgt="Privet   mir",
            after_tgt="Privet mir",
            original_src="Hello   world",
            original_tgt="Privet   mir",
        ),
        Proposal(
            proposal_id=make_cleanup_proposal_id(2, "dedup_tu", 0),
            kind="cleanup",
            tu_index=2,
            rule="dedup_tu",
            message="Duplicate TU removed",
            before_src="A",
            after_src="",
            before_tgt="B",
            after_tgt="",
            original_src="A",
            original_tgt="B",
        ),
    ]
    plan = RepairPlan(input_path="mixed.tmx", total_tus=3, proposals=proposals)
    file = FilePlanResult(
        input_path=Path("mixed.tmx"),
        output_path=Path("mixed_repaired.tmx"),
        report_path=None,
        html_report_path=Path("mixed.html"),
        xlsx_report_path=Path("mixed.xlsx"),
        stats=RepairStats(
            total_tus=3, split_tus=0, created_tus=3,
            src_lang="en-US", tgt_lang="ru-RU", skipped_tus=0,
        ),
        plan=plan,
    )
    return PlanPhaseResult(files=[file])


def _iter_proposal_items(dialog: ReviewDialog):
    tree = dialog._tree
    for i in range(tree.topLevelItemCount()):
        file_item = tree.topLevelItem(i)
        for j in range(file_item.childCount()):
            group_item = file_item.child(j)
            for k in range(group_item.childCount()):
                yield group_item.child(k)


def _iter_group_items(dialog: ReviewDialog):
    tree = dialog._tree
    for i in range(tree.topLevelItemCount()):
        file_item = tree.topLevelItem(i)
        for j in range(file_item.childCount()):
            yield file_item.child(j)


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


def test_type_filter_checkbox_matrix_and_counts(qapp):
    plans = _make_mixed_plans()
    dialog = ReviewDialog(plans)

    assert "split" in dialog._type_filter_checkboxes
    assert "normalize_spaces" in dialog._type_filter_checkboxes
    assert "dedup_tu" in dialog._type_filter_checkboxes

    assert dialog._type_filter_counts["split"] == 1
    assert dialog._type_filter_counts["normalize_spaces"] == 1
    assert dialog._type_filter_counts["dedup_tu"] == 1

    dialog._set_type_filter_checked_keys({"normalize_spaces"})
    visible = [item for item in _iter_proposal_items(dialog) if not item.isHidden()]
    assert len(visible) == 1
    proposal = visible[0].data(0, PROPOSAL_ROLE)
    assert isinstance(proposal, Proposal)
    assert proposal.kind == "cleanup"
    assert proposal.rule == "normalize_spaces"


def test_type_filter_all_checkbox_goes_partially_checked(qapp):
    plans = _make_mixed_plans()
    dialog = ReviewDialog(plans)

    dialog._set_type_filter_checked_keys({"split"})
    assert dialog._type_filter_all_checkbox.checkState() == Qt.CheckState.PartiallyChecked


def test_dialog_builds_type_groups(qapp):
    plans = _make_mixed_plans()
    dialog = ReviewDialog(plans)

    groups = list(_iter_group_items(dialog))
    assert len(groups) == 3
    group_types = {str(item.data(0, TYPE_ROLE)) for item in groups}
    assert group_types == {"split", "normalize_spaces", "dedup_tu"}
    assert all(bool(item.data(0, GROUP_ROLE)) for item in groups)


def test_reject_current_group_bulk_button(qapp):
    plans = _make_mixed_plans()
    dialog = ReviewDialog(plans)
    groups = list(_iter_group_items(dialog))
    split_group = next(item for item in groups if str(item.data(0, TYPE_ROLE)) == "split")
    dialog._tree.setCurrentItem(split_group)

    dialog._bulk_set(False, scope="group")

    split_items = [split_group.child(idx) for idx in range(split_group.childCount())]
    assert all(item.checkState(0) == Qt.CheckState.Unchecked for item in split_items)
    assert all(item.data(0, PROPOSAL_ROLE).accepted is False for item in split_items)
    non_split_items = [item for item in _iter_proposal_items(dialog) if item not in split_items]
    assert all(item.data(0, PROPOSAL_ROLE).accepted is True for item in non_split_items)


def test_preview_is_git_style_diff(qapp):
    plans = _make_mixed_plans()
    dialog = ReviewDialog(plans)
    cleanup_item = next(
        item
        for item in _iter_proposal_items(dialog)
        if item.data(0, PROPOSAL_ROLE).rule == "normalize_spaces"
    )
    dialog._tree.setCurrentItem(cleanup_item)

    src_preview = dialog._preview_src.toPlainText()
    tgt_preview = dialog._preview_tgt.toPlainText()
    assert "-" in src_preview and "+" in src_preview
    assert "-" in tgt_preview and "+" in tgt_preview


def test_inline_diff_segments_highlight_only_changed_fragments():
    before = '<bpt i="1" type="bold"></bpt>PRESS RELEASE'
    after = "PRESS RELEASE"

    before_segments, after_segments = _inline_diff_segments(before, after)

    before_deleted = "".join(text for text, kind in before_segments if kind == "delete")
    before_equal = "".join(text for text, kind in before_segments if kind == "equal")
    after_inserted = "".join(text for text, kind in after_segments if kind == "insert")

    assert "<bpt" in before_deleted
    assert "PRESS RELEASE" in before_equal
    assert after_inserted == ""
