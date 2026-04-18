# TMX Repair GUI Refactor (DESIGN.md Aligned) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the PySide6 GUI into modular panels + controller, and restyle it to match `DESIGN.md` ("Precision Architect") without changing core TMX processing behavior.

**Architecture:** Keep `RepairWorker` and `ReviewDialog` contracts intact; move window orchestration from monolithic `MainWindow` into `RunController`; split UI sections into focused widgets; apply centralized design tokens + stylesheet builder to enforce tonal/no-line editorial style.

**Tech Stack:** Python 3.13, PySide6, pytest, existing `ui/*`, `core/*`.

---

### Task 1: Introduce Centralized Design Tokens and App Stylesheet

**Files:**
- Create: `ui/theme.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_theme_tokens.py`

- [ ] **Step 1: Write the failing test for tokens and stylesheet API**

```python
# tests/test_theme_tokens.py
from ui.theme import TOKENS, build_app_stylesheet


def test_design_tokens_match_spec_core_values():
    assert TOKENS["primary"] == "#056687"
    assert TOKENS["primary_dim"] == "#005977"
    assert TOKENS["surface"] == "#f8f9fa"
    assert TOKENS["surface_low"] == "#f1f4f6"
    assert TOKENS["surface_lowest"] == "#ffffff"
    assert TOKENS["inverse_surface"] == "#0c0f10"


def test_stylesheet_contains_editorial_rules():
    css = build_app_stylesheet()
    assert "QGroupBox" in css
    assert "QLineEdit" in css
    assert "QTextEdit" in css
    assert "gradient" in css.lower() or "#056687" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_theme_tokens.py -q`  
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.theme'`.

- [ ] **Step 3: Implement `ui/theme.py` with tokens + builder**

```python
# ui/theme.py
TOKENS: dict[str, str] = {
    "primary": "#056687",
    "primary_dim": "#005977",
    "surface": "#f8f9fa",
    "surface_low": "#f1f4f6",
    "surface_lowest": "#ffffff",
    "surface_high": "#e3e9ec",
    "surface_highest": "#dbe4e7",
    "outline_variant": "#abb3b7",
    "inverse_surface": "#0c0f10",
    "inverse_on_surface": "#9b9d9e",
}


def build_app_stylesheet() -> str:
    return f"""
    QMainWindow, QWidget {{
        background: {TOKENS['surface']};
        color: #2b3437;
        font-size: 13px;
    }}
    QGroupBox {{
        background: {TOKENS['surface_lowest']};
        border: none;
        border-radius: 10px;
        margin-top: 10px;
        padding: 12px;
    }}
    QLineEdit, QTextEdit {{
        background: {TOKENS['surface_high']};
        border: none;
        border-radius: 8px;
    }}
    QPushButton[role="primary"] {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {TOKENS['primary']}, stop:1 {TOKENS['primary_dim']});
        color: #f1f9ff;
        border: none;
        border-radius: 8px;
    }}
    """
```

- [ ] **Step 4: Wire stylesheet usage in `MainWindow`**

```python
# ui/main_window.py
from ui.theme import build_app_stylesheet

def _apply_minimal_style(self) -> None:
    self.setStyleSheet(build_app_stylesheet())
```

- [ ] **Step 5: Re-run tests**

Run: `python -m pytest tests/test_theme_tokens.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/theme.py ui/main_window.py tests/test_theme_tokens.py
git commit -m "refactor(gui): add centralized design tokens and stylesheet builder"
```

---

### Task 2: Extract Files Panel Widget

**Files:**
- Create: `ui/widgets/files_panel.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_files_panel.py`

- [ ] **Step 1: Write failing test for FilesPanel public API**

```python
# tests/test_files_panel.py
from pathlib import Path
from ui.widgets.files_panel import FilesPanel


def test_files_panel_set_get_paths(qtbot):
    panel = FilesPanel()
    qtbot.addWidget(panel)
    panel.set_input_paths([Path("a.tmx"), Path("b.tmx")])
    out = panel.input_paths()
    assert [p.name for p in out] == ["a.tmx", "b.tmx"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_files_panel.py -q`  
Expected: FAIL with import error.

- [ ] **Step 3: Implement `FilesPanel`**

```python
# ui/widgets/files_panel.py
class FilesPanel(QWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.drop_zone = DropZone()
        self.input_edit = QTextEdit()
        self.output_edit = QLineEdit()
        # compose internal layout (drop zone + input list + output directory)

    def input_paths(self) -> list[Path]:
        # parse lines through normalize_input_path/normalize_path_obj
        ...

    def set_input_paths(self, paths: list[Path]) -> None:
        ...

    def output_dir(self) -> Path | None:
        text = self.output_edit.text().strip()
        return Path(text) if text else None
```

- [ ] **Step 4: Integrate panel in `MainWindow` without behavior changes**

```python
# ui/main_window.py
from ui.widgets.files_panel import FilesPanel

self.files_panel = FilesPanel()
self.files_panel.files_dropped.connect(self._on_files_dropped)
# replace old input/output widgets usage with self.files_panel API
```

- [ ] **Step 5: Re-run tests**

Run: `python -m pytest tests/test_files_panel.py tests/test_main_paths.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/widgets/files_panel.py ui/main_window.py tests/test_files_panel.py
git commit -m "refactor(gui): extract files panel widget"
```

---

### Task 3: Extract Processing, Gemini, and Reports Panels

**Files:**
- Create: `ui/widgets/stages_panel.py`
- Create: `ui/widgets/gemini_panel.py`
- Create: `ui/widgets/reports_panel.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_settings_panels.py`

- [ ] **Step 1: Write failing test for settings panel value objects**

```python
# tests/test_settings_panels.py
from ui.widgets.stages_panel import StagesPanel
from ui.widgets.gemini_panel import GeminiPanel
from ui.widgets.reports_panel import ReportsPanel


def test_stages_panel_defaults(qtbot):
    panel = StagesPanel()
    qtbot.addWidget(panel)
    cfg = panel.values()
    assert cfg.enable_split is True
    assert cfg.enable_cleanup_service_markup is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_settings_panels.py -q`  
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `StagesPanel`**

```python
# ui/widgets/stages_panel.py
@dataclass
class StageSettings:
    enable_split: bool
    enable_cleanup_spaces: bool
    enable_cleanup_service_markup: bool
    enable_cleanup_garbage: bool
    enable_cleanup_warnings: bool

class StagesPanel(QWidget):
    def values(self) -> StageSettings:
        return StageSettings(
            enable_split=self.enable_split_checkbox.isChecked(),
            enable_cleanup_spaces=self.enable_cleanup_spaces_checkbox.isChecked(),
            enable_cleanup_service_markup=self.enable_cleanup_service_markup_checkbox.isChecked(),
            enable_cleanup_garbage=self.enable_cleanup_garbage_checkbox.isChecked(),
            enable_cleanup_warnings=self.enable_cleanup_warnings_checkbox.isChecked(),
        )
```

- [ ] **Step 4: Implement `GeminiPanel` and `ReportsPanel`**

```python
# ui/widgets/gemini_panel.py
@dataclass
class GeminiSettings:
    verify_with_gemini: bool
    gemini_api_key: str
    gemini_model: str
    gemini_input_price_per_1m: float
    gemini_output_price_per_1m: float

# ui/widgets/reports_panel.py
@dataclass
class ReportSettings:
    log_file: str | None
    report_dir: Path | None
    html_report_dir: Path | None
    xlsx_report_dir: Path | None
```

- [ ] **Step 5: Integrate all panels into `MainWindow` and replace old field reads**

```python
# ui/main_window.py
stage = self.stages_panel.values()
gemini = self.gemini_panel.values()
reports = self.reports_panel.values()
# use these objects to build RepairRunConfig
```

- [ ] **Step 6: Re-run tests**

Run: `python -m pytest tests/test_settings_panels.py tests/test_main_window_flow.py -q`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ui/widgets/stages_panel.py ui/widgets/gemini_panel.py ui/widgets/reports_panel.py ui/main_window.py tests/test_settings_panels.py
git commit -m "refactor(gui): extract settings panels (stages/gemini/reports)"
```

---

### Task 4: Extract Status Panel and Condensed GUI Logging Surface

**Files:**
- Create: `ui/widgets/status_panel.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_status_panel.py`

- [ ] **Step 1: Write failing test for status panel render methods**

```python
# tests/test_status_panel.py
from ui.widgets.status_panel import StatusPanel


def test_status_panel_updates_text(qtbot):
    panel = StatusPanel()
    qtbot.addWidget(panel)
    panel.set_status("running")
    panel.set_progress("file 1/3")
    panel.set_usage(10, 5, 15, 0.001)
    assert "running" in panel.status_text()
    assert "1/3" in panel.progress_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_status_panel.py -q`  
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `StatusPanel`**

```python
# ui/widgets/status_panel.py
class StatusPanel(QWidget):
    def set_status(self, text: str) -> None: ...
    def set_progress(self, text: str) -> None: ...
    def set_usage(self, in_tokens: int, out_tokens: int, total_tokens: int, cost: float) -> None: ...
    def set_rate(self, now_rate: float, avg_rate: float, forecast: float) -> None: ...
    def append_log(self, message: str) -> None: ...
```

- [ ] **Step 4: Replace direct label/textedit writes in `MainWindow`**

```python
# ui/main_window.py
self.status_panel.set_status(...)
self.status_panel.set_progress(...)
self.status_panel.set_usage(...)
self.status_panel.append_log(...)
```

- [ ] **Step 5: Re-run tests**

Run: `python -m pytest tests/test_status_panel.py tests/test_main_window_flow.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/widgets/status_panel.py ui/main_window.py tests/test_status_panel.py
git commit -m "refactor(gui): extract status panel and logging surface"
```

---

### Task 5: Add `ViewState` and Central Form-State Mapping

**Files:**
- Create: `ui/state/view_state.py`
- Create: `ui/state/__init__.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_view_state_mapping.py`

- [ ] **Step 1: Write failing test for state mapping round-trip**

```python
# tests/test_view_state_mapping.py
from ui.state.view_state import ViewState


def test_view_state_defaults():
    state = ViewState.defaults()
    assert state.verify_with_gemini is False
    assert state.enable_split is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_view_state_mapping.py -q`  
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `ViewState`**

```python
# ui/state/view_state.py
@dataclass
class ViewState:
    input_paths: list[Path]
    output_dir: Path | None
    dry_run: bool
    enable_split: bool
    enable_cleanup_spaces: bool
    enable_cleanup_service_markup: bool
    enable_cleanup_garbage: bool
    enable_cleanup_warnings: bool
    verify_with_gemini: bool
    gemini_model: str
    gemini_input_price_per_1m: float
    gemini_output_price_per_1m: float

    @classmethod
    def defaults(cls) -> "ViewState":
        return cls(input_paths=[], output_dir=None, dry_run=False, enable_split=True, ...)
```

- [ ] **Step 4: Use state mapper in `MainWindow`**

```python
# ui/main_window.py
def _read_view_state(self) -> ViewState: ...
def _apply_view_state(self, state: ViewState) -> None: ...
```

- [ ] **Step 5: Re-run tests**

Run: `python -m pytest tests/test_view_state_mapping.py tests/test_main_window_flow.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/state/view_state.py ui/state/__init__.py ui/main_window.py tests/test_view_state_mapping.py
git commit -m "refactor(gui): add view state model and mapping"
```

---

### Task 6: Introduce RunController and Move Plan/Apply Orchestration

**Files:**
- Create: `ui/controllers/run_controller.py`
- Create: `ui/controllers/__init__.py`
- Modify: `ui/main_window.py`
- Modify: `tests/test_main_window_flow.py`
- Create: `tests/test_run_controller.py`

- [ ] **Step 1: Write failing controller flow test**

```python
# tests/test_run_controller.py
from ui.controllers.run_controller import RunController


def test_controller_requires_config_before_start():
    controller = RunController()
    try:
        controller.start_plan_phase(None)
        assert False, "Expected ValueError"
    except ValueError:
        assert True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_run_controller.py -q`  
Expected: FAIL (module missing).

- [ ] **Step 3: Implement controller with explicit callbacks**

```python
# ui/controllers/run_controller.py
class RunController(QObject):
    plan_started = Signal()
    plans_ready = Signal(object)
    apply_started = Signal()
    apply_completed = Signal(object)
    failed = Signal(str)

    def start_plan_phase(self, config: RepairRunConfig) -> None: ...
    def start_apply_phase(self, config: RepairRunConfig, plans: PlanPhaseResult) -> None: ...
```

- [ ] **Step 4: Move orchestration methods from `MainWindow` to controller usage**

```python
# ui/main_window.py
self.controller = RunController(parent=self)
self.controller.plans_ready.connect(self._on_plans_ready)
self.controller.apply_completed.connect(self._on_worker_completed)
self.controller.failed.connect(self._on_worker_failed)
```

- [ ] **Step 5: Update and run flow tests**

Run: `python -m pytest tests/test_run_controller.py tests/test_main_window_flow.py tests/test_review_dialog.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/controllers/run_controller.py ui/controllers/__init__.py ui/main_window.py tests/test_run_controller.py tests/test_main_window_flow.py
git commit -m "refactor(gui): move plan/apply orchestration into run controller"
```

---

### Task 7: Rebuild MainWindow Shell to Match `DESIGN.md` Structure

**Files:**
- Modify: `ui/main_window.py`
- Modify: `ui/theme.py`
- Test: `tests/test_main_window_shell.py`

- [ ] **Step 1: Write failing shell structure test**

```python
# tests/test_main_window_shell.py
from ui.main_window import MainWindow


def test_main_window_has_left_rail_and_prompt_page(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert hasattr(w, "nav_repair_button")
    assert hasattr(w, "nav_prompt_button")
    assert hasattr(w, "status_strip_label")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main_window_shell.py -q`  
Expected: FAIL (attributes missing).

- [ ] **Step 3: Implement rail + canvas + status strip composition**

```python
# ui/main_window.py
# high-level structure:
# [left_rail | main_canvas]
# main_canvas = [top_bar, stacked_content, status_strip]
self.root_split = QHBoxLayout(...)
self.nav_repair_button = QPushButton("Repair")
self.nav_prompt_button = QPushButton("Gemini Prompt")
self.status_strip_label = QLabel("TMX REPAIR | TOKENS: 0 | COST: $0.000000")
```

- [ ] **Step 4: Apply no-line/tonal style rules**

```python
# ui/theme.py
# ensure section separation by background tones, avoid hard 1px separators
# except fallback low-alpha outline where needed for accessibility
```

- [ ] **Step 5: Re-run tests**

Run: `python -m pytest tests/test_main_window_shell.py tests/test_main_window_flow.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/main_window.py ui/theme.py tests/test_main_window_shell.py
git commit -m "feat(gui): implement design-system shell (left rail, canvas, status strip)"
```

---

### Task 8: End-to-End Regression and Final Verification

**Files:**
- Modify: `tests/test_main_window_flow.py`
- Modify: `tests/test_review_dialog.py`
- Optional docs note: `docs/superpowers/specs/2026-04-17-tmx-repair-gui-refactor-design.md` (only if acceptance wording needs sync)

- [ ] **Step 1: Add regression for edited prompt propagation**

```python
# tests/test_main_window_flow.py
def test_edited_prompt_is_used_for_run(monkeypatch, qapp):
    window = MainWindow()
    window.prompt_editor.setPlainText("CUSTOM_PROMPT_X")
    # trigger config build path, assert gemini_prompt_template == "CUSTOM_PROMPT_X"
```

- [ ] **Step 2: Add regression for concise progress labels (batch/file level)**

```python
def test_progress_label_updates_on_file_events(qapp):
    window = MainWindow()
    window._on_progress_event({"event": "file_start", "file_index": 1, "file_total": 3, "input_path": "a.tmx"})
    assert "1/3" in window.progress_label.text() or "1/3" in window.status_panel.progress_text()
```

- [ ] **Step 3: Run focused GUI test pack**

Run: `python -m pytest tests/test_main_window_flow.py tests/test_review_dialog.py tests/test_main_paths.py -q`  
Expected: PASS.

- [ ] **Step 4: Run full regression suite**

Run: `python -m pytest -q`  
Expected: PASS all tests (existing and new).

- [ ] **Step 5: Commit**

```bash
git add tests/test_main_window_flow.py tests/test_review_dialog.py
git commit -m "test(gui): add prompt propagation and progress regressions"
```

---

## Spec Coverage Check

- Modularization of monolithic GUI: covered by Tasks 2, 3, 4, 5, 6.
- Run orchestration separation: covered by Task 6.
- DESIGN.md visual alignment: covered by Tasks 1 and 7.
- Reduced clipping/overlap risk via layout rebuild: covered by Task 7.
- Concise logging and runtime counters: covered by Task 4 and Task 8.
- Prompt editor runtime correctness: covered by Task 8.

## Placeholder Scan

- No `TODO`/`TBD` placeholders in task steps.
- Every task has explicit file targets, commands, and expected results.

## Type/Contract Consistency Check

- `RepairRunConfig`, `PlanPhaseResult`, `BatchRunResult` remain the canonical run contracts.
- `RunController` orchestrates workers but does not alter worker public inputs/outputs.
- `ReviewDialog` remains in the plan/apply gap and keeps proposal-acceptance behavior.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-17-gui-refactor-design-system-plan.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
