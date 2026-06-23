# UI Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the existing PySide6 interface with consistent sizing, states, surfaces, Russian text, stable numeric labels, keyboard-accessible drop handling, and subtle page fades without changing application workflows or assets.

**Architecture:** Keep visual policy in `ui/theme.py`. Add two focused helpers: one stacked widget for page fades and one surface-shadow function. Extend the existing `DropZone` and `StatusPanel` in place, then translate only user-facing UI strings.

**Tech Stack:** Python 3.11+, PySide6, pytest, Qt offscreen platform.

---

### Task 1: Shared theme, hit areas, and surface depth

**Files:**
- Create: `ui/widgets/surface_effects.py`
- Modify: `ui/theme.py`
- Modify: `ui/main_window.py`
- Modify: `ui/widgets/stages_panel.py`
- Test: `tests/test_theme_tokens.py`
- Test: `tests/test_main_window_shell.py`

- [ ] **Step 1: Write failing theme and surface tests**

Add assertions that the generated stylesheet contains `min-height: 40px`, explicit `QPushButton:focus`, `QPushButton:disabled`, `QLineEdit:disabled`, and `QFrame#dropZone[dragActive="true"]`. Add a shell assertion that every visible non-navigation button is at least 40 px high after layout, help buttons are at least 40 x 40 px, and `CanvasTopBar` has a `QGraphicsDropShadowEffect`.

```python
def test_app_stylesheet_has_accessible_interaction_states() -> None:
    stylesheet = build_app_stylesheet()
    assert "min-height: 40px" in stylesheet
    assert "QPushButton:focus" in stylesheet
    assert "QPushButton:disabled" in stylesheet
    assert "QLineEdit:disabled" in stylesheet
    assert 'QFrame#dropZone[dragActive="true"]' in stylesheet
```

```python
def test_visible_buttons_have_minimum_hit_height(qapp):
    window = MainWindow()
    window.show()
    qapp.processEvents()
    try:
        buttons = [
            button
            for button in window.findChildren(QPushButton)
            if button.isVisibleTo(window)
        ]
        assert buttons
        assert all(button.height() >= 40 for button in buttons)
        top_bar = window.findChild(QWidget, "CanvasTopBar")
        assert isinstance(top_bar.graphicsEffect(), QGraphicsDropShadowEffect)
    finally:
        window.close()
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_tokens.py tests\test_main_window_shell.py -q`

Expected: failures for missing 40 px rules, states, and shadow effect.

- [ ] **Step 3: Implement centralized visual rules and surface helper**

Create the complete helper:

```python
"""Small visual effects shared by major application surfaces."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget


def apply_surface_shadow(widget: QWidget) -> QGraphicsDropShadowEffect:
    """Apply restrained depth to a major card-like surface."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(18.0)
    effect.setOffset(0.0, 3.0)
    effect.setColor(QColor(0, 0, 0, 28))
    widget.setGraphicsEffect(effect)
    return effect
```

Update QSS control heights to 40 px, add focus/disabled/pressed rules, style `#dropZone`, and use shared radius values consistently. Apply the helper to the top bar and the major prompt/status cards. Change the transport button fixed size to 44 x 40 and help button fixed size to 40 x 40 without changing icon sizes.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_theme_tokens.py tests\test_main_window_shell.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add ui/theme.py ui/main_window.py ui/widgets/stages_panel.py ui/widgets/surface_effects.py tests/test_theme_tokens.py tests/test_main_window_shell.py
git commit -m "Polish shared UI controls and surfaces"
```

### Task 2: Accessible and stateful drop zone

**Files:**
- Modify: `ui/drop_zone.py`
- Create: `tests/test_drop_zone.py`

- [ ] **Step 1: Write failing keyboard and drag-state tests**

```python
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.drop_zone import DropZone


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_drop_zone_activates_from_keyboard(qapp):
    zone = DropZone()
    clicks: list[bool] = []
    zone.clicked.connect(lambda: clicks.append(True))
    zone.show()
    zone.setFocus()
    QTest.keyClick(zone, Qt.Key.Key_Return)
    QTest.keyClick(zone, Qt.Key.Key_Space)
    assert clicks == [True, True]


def test_drop_zone_exposes_accessible_drag_state(qapp):
    zone = DropZone()
    assert zone.accessibleName() == "Добавить TMX-файлы"
    assert zone.property("dragActive") is False
    zone._set_drag_active(True)
    assert zone.property("dragActive") is True
    zone._set_drag_active(False)
    assert zone.property("dragActive") is False
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_drop_zone.py -q`

Expected: failures because keyboard activation, accessible name, and `_set_drag_active` do not exist.

- [ ] **Step 3: Implement keyboard and drag behavior**

Remove the inline stylesheet. Set `StrongFocus`, `accessibleName`, and the initial `dragActive=False` property. Add `_set_drag_active(active: bool)` that updates the property and calls `style().unpolish(self)`, `style().polish(self)`, and `update()`. Override `keyPressEvent`, `dragLeaveEvent`, and update drag enter/move/drop paths so only valid TMX payloads activate the state and every leave/drop resets it.

```python
def keyPressEvent(self, event) -> None:  # type: ignore[override]
    if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space):
        self.clicked.emit()
        event.accept()
        return
    super().keyPressEvent(event)


def _set_drag_active(self, active: bool) -> None:
    if bool(self.property("dragActive")) == active:
        return
    self.setProperty("dragActive", active)
    self.style().unpolish(self)
    self.style().polish(self)
    self.update()
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_drop_zone.py tests\test_files_panel.py -q`

Expected: all tests pass and existing file normalization remains unchanged.

- [ ] **Step 5: Commit**

```powershell
git add ui/drop_zone.py tests/test_drop_zone.py
git commit -m "Make the TMX drop zone accessible"
```

### Task 3: Interruptible page fades

**Files:**
- Create: `ui/widgets/fading_stack.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_main_window_shell.py`

- [ ] **Step 1: Write failing stacked-widget tests**

```python
def test_page_stack_skips_initial_animation_and_animates_navigation(qapp):
    window = MainWindow()
    assert isinstance(window.page_stack, FadingStackedWidget)
    assert window.page_stack.is_animating() is False

    window.nav_prompt_button.click()
    assert window.page_stack.currentWidget() is window.prompt_tab
    assert window.page_stack.is_animating() is True

    window.nav_logs_button.click()
    assert window.page_stack.currentWidget() is window.logs_tab
    assert window.page_stack.is_animating() is True
    window.close()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_main_window_shell.py::test_page_stack_skips_initial_animation_and_animates_navigation -q`

Expected: failure because `FadingStackedWidget` is missing.

- [ ] **Step 3: Implement the focused component**

Create `FadingStackedWidget` with `ANIMATION_DURATION_MS = 140`, `set_current_index(index: int, *, animate: bool)`, and `is_animating()`. Stop any active animation before switching. Remove the old page effect, select the new index, attach `QGraphicsOpacityEffect`, and animate opacity from 0.0 to 1.0 with `QEasingCurve.OutCubic`. On completion restore opacity and remove the effect. In `MainWindow`, instantiate this class and call `animate=False` only for `_switch_page(0)` during shell construction; navigation clicks use `animate=True`.

- [ ] **Step 4: Run the shell tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_main_window_shell.py -q`

Expected: all shell tests pass, including rapid navigation.

- [ ] **Step 5: Commit**

```powershell
git add ui/widgets/fading_stack.py ui/main_window.py tests/test_main_window_shell.py
git commit -m "Add subtle interruptible page fades"
```

### Task 4: Stable numeric status presentation

**Files:**
- Modify: `ui/widgets/status_panel.py`
- Test: `tests/test_status_panel.py`

- [ ] **Step 1: Write failing localization and fixed-pitch tests**

```python
from PySide6.QtGui import QFontInfo


def test_status_panel_uses_russian_labels_and_stable_numeric_font(qapp):
    panel = StatusPanel()
    panel.set_status("выполняется")
    panel.set_progress("файл 1/3")
    panel.set_elapsed("00:07")
    assert panel.status_text().startswith("Статус:")
    assert panel.progress_text().startswith("Прогресс:")
    assert panel.elapsed_text().startswith("Время:")
    assert QFontInfo(panel.usage_label.font()).fixedPitch()
    assert QFontInfo(panel.rate_label.font()).fixedPitch()
    assert QFontInfo(panel.elapsed_label.font()).fixedPitch()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_status_panel.py -q`

Expected: failures for English prefixes and proportional fonts.

- [ ] **Step 3: Implement Russian prefixes and numeric font**

Use `QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)`, copy the application font point size, and apply it only to usage, rate, and elapsed labels. Translate prefixes to `Статус`, `Прогресс`, `Скорость`, and `Время`; keep Gemini and units unchanged.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_status_panel.py tests\test_main_window_flow.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add ui/widgets/status_panel.py tests/test_status_panel.py tests/test_main_window_flow.py
git commit -m "Stabilize and localize runtime status labels"
```

### Task 5: Complete Russian UI localization

**Files:**
- Modify: `ui/main_window.py`
- Modify: `ui/review_view.py`
- Modify: `ui/widgets/gemini_settings_dialog.py`
- Modify: `tmx2csv_app/gui.py`
- Test: `tests/test_main_window_shell.py`
- Test: `tests/test_review_dialog.py`
- Test: `tests/test_gemini_settings_dialog.py`
- Create: `tests/test_converter_ui_text.py`

- [ ] **Step 1: Write failing visible-text tests**

Test exact navigation titles/tooltips and review controls. Instantiate `ConvertTab`, `CleanTab`, and `ExcelToTmxTab`, collect visible `QLabel`, `QPushButton`, and `QCheckBox` text, and assert the former English labels are absent.

```python
def test_main_navigation_uses_russian_text(qapp):
    window = MainWindow()
    assert window._page_titles == {
        0: "Исправление",
        1: "Промпт Gemini",
        2: "Журнал",
        3: "Конвертация",
        4: "Очистка",
        5: "Excel → TMX",
    }
    assert window.nav_repair_button.toolTip() == "Исправление"
    assert window.nav_logs_button.toolTip() == "Журнал"
    window.close()
```

```python
def test_review_navigation_uses_russian_text(qapp):
    dialog = ReviewDialog(_make_plans())
    assert dialog._page_prev_button.text() == "Назад"
    assert dialog._page_next_button.text() == "Далее"
    assert dialog._status_buttons["accepted"].text() == "Принятые"
    assert dialog._status_buttons["rejected"].text() == "Отклонённые"
```

Apply these exact replacements while leaving technical identifiers unchanged:

| Existing text | Russian text |
| --- | --- |
| Repair | Исправление |
| Gemini Prompt | Промпт Gemini |
| Logs | Журнал |
| Convert | Конвертация |
| Clean | Очистка |
| Reset Prompt | Сбросить промпт |
| Copy Prompt | Копировать промпт |
| Batch Repair Completed | Обработка завершена |
| Open Files Folder | Открыть папку файлов |
| Open Reports Folder | Открыть папку отчётов |
| Close | Закрыть |
| Accepted / Rejected | Принятые / Отклонённые |
| Prev / Next | Назад / Далее |
| Source diff / Target diff | Изменения источника / Изменения перевода |
| Idle / Ready | Ожидание / Готово |
| Processing / Scanning / Cleaning | Обработка / Сканирование / Очистка |
| Finished / Failed | Завершено / Ошибки |

- [ ] **Step 2: Run localization tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_main_window_shell.py tests\test_review_dialog.py tests\test_gemini_settings_dialog.py tests\test_converter_ui_text.py -q`

Expected: failures containing the current English labels.

- [ ] **Step 3: Replace only user-facing strings**

Update literals in the listed UI files. Do not alter internal keys (`accepted`, `rejected`, `split`), serialized values, model names, file extensions, raw log payloads, or exception messages. Do not modify `asset/`.

- [ ] **Step 4: Run localization and functional tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_main_window_shell.py tests\test_review_dialog.py tests\test_gemini_settings_dialog.py tests\test_converter_ui_text.py tests\test_main_window_flow.py -q`

Expected: all selected tests pass with unchanged actions.

- [ ] **Step 5: Commit**

```powershell
git add ui/main_window.py ui/review_view.py ui/widgets/gemini_settings_dialog.py tmx2csv_app/gui.py tests/test_main_window_shell.py tests/test_review_dialog.py tests/test_gemini_settings_dialog.py tests/test_converter_ui_text.py
git commit -m "Localize the desktop interface in Russian"
```

### Task 6: Full verification and visual QA

**Files:**
- Modify only files required by a reproduced failing test.
- Do not modify: `asset/**`

- [ ] **Step 1: Run the complete suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass. Pytest cache permission warnings may be reported by the managed Windows sandbox; test failures are not acceptable.

- [ ] **Step 2: Verify source quality and asset integrity**

Run: `.\.venv\Scripts\python.exe -m compileall main.py ui tmx2csv_app`

Expected: compilation succeeds.

Run: `git diff --check`

Expected: no output.

Run: `git diff 931002c -- asset`

Expected: no output.

- [ ] **Step 3: Generate and inspect an offscreen screenshot**

Instantiate `MainWindow` with `QT_QPA_PLATFORM=offscreen`, resize to 1260 x 820, process events, and save `out/tmx-repair-ui-polished.png`. Verify that controls do not clip, focus/disabled states are legible, shadows are not cut off, and Russian labels fit.

- [ ] **Step 4: Correct only reproduced visual defects**

For every defect, first add a focused failing geometry or text assertion, run it to verify RED, apply the minimal correction, and rerun to GREEN.

- [ ] **Step 5: Commit final verification corrections if needed**

```powershell
git add ui tests tmx2csv_app
git commit -m "Finalize UI polish verification"
```

