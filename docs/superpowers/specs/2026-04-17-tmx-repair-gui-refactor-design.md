# TMX Repair GUI Refactor Design

**Date:** 2026-04-17  
**Status:** Draft for user review (aligned to `DESIGN.md`)  
**Scope:** UI/UX and GUI architecture refactor only (no core TMX logic changes)

## 1. Context and Problem Statement

Current GUI behavior is functionally rich, but ergonomics and maintainability are weak:

- The main window is monolithic (`ui/main_window.py`) and mixes layout, state, validation, run orchestration, and progress rendering.
- Fixed-size tendencies and dense vertical stacking lead to broken row heights and visual overlap at reduced window sizes.
- Settings are hard to scan because controls are numerous and not grouped as a clear workflow.
- Runtime flow (`plan -> review -> apply`) is correct but distributed between UI and worker in a way that is hard to evolve safely.

Goal: redesign the GUI to be modern, compact, readable, and modular, while preserving current pipeline behavior.

## 2. Goals and Non-Goals

### Goals

- Keep all existing processing capabilities (split, cleanup modes, Gemini verification, reports).
- Make UI adaptive to smaller window sizes without clipping/overlap.
- Separate responsibilities into focused UI modules and a run controller.
- Keep the two-phase controlled workflow (`plan/review/apply`) unchanged logically.
- Keep GUI logging concise (batch/file phase progress), with detailed diagnostics in log file.
- Apply visual system from `DESIGN.md` as mandatory style baseline for the refactor.

### Non-Goals

- No changes to TMX repair/split/cleanup algorithms in `core/*`.
- No changes to report formats or output data contracts.
- No migration to a new UI framework (stay on current PySide6 stack).

## 3. Proposed Architecture (Recommended Option)

Recommended approach: **Modular UI + Run Controller**.

### 3.1 Module Boundaries

- `ui/main_window.py`
  - Shell only: window frame, tab container, top-level composition.
  - No business orchestration logic.
- `ui/widgets/files_panel.py`
  - Input files, drag-drop, output directory controls.
- `ui/widgets/stages_panel.py`
  - Processing stage toggles (split + cleanup/warnings).
- `ui/widgets/gemini_panel.py`
  - Gemini enable/model/key/pricing controls.
- `ui/widgets/reports_panel.py`
  - Report/log path settings.
- `ui/widgets/status_panel.py`
  - Progress lines, concise live usage, GUI log box.
- `ui/controllers/run_controller.py`
  - Collect config -> validate -> start worker (plan) -> open review -> start worker (apply).
  - Single orchestration point for lifecycle and error handling.
- `ui/state/view_state.py`
  - Dataclass state snapshot for form values and run status.

### 3.2 Existing Modules Preserved

- `ui/review_view.py` remains the approval dialog.
- `ui/worker.py` remains async execution unit (plan/apply phases).
- `ui/types.py` remains typed config/result contracts, with optional extension for UI state.

## 4. UX and Layout Design

### 4.1 Information Architecture

Main tab (`Repair`) is split into three visual zones:

1. **Files zone (top, compact)**
   - Drag-and-drop area
   - Input list
   - Output root

2. **Settings zone (middle, cards)**
   - Processing stages card
   - Gemini card
   - Reports card
   - Run mode card

3. **Runtime zone (bottom)**
   - Status line
   - Batch progress line
   - Token/cost line
   - Compact log text area

Prompt remains in separate tab (`Gemini Prompt`) with explicit label that edited prompt is the effective runtime prompt.

### 4.2 Visual Style Principles

- Minimalist neutral palette, strong spacing rhythm, no dense stacked form rows.
- Avoid fixed heights for interactive rows where possible.
- Preserve only minimum heights for critical controls; remove max-height constraints that cause clipping.
- Keep section cards with clear titles and concise helper text.
- Follow the "No-Line Rule": avoid section-defining 1px dividers, separate areas by tonal surfaces.

### 4.3 Visual Tokens from `DESIGN.md`

Palette and surface hierarchy (mandatory defaults):

- `primary`: `#056687`
- `primary_dim`: `#005977`
- `surface`: `#f8f9fa`
- `surface-container-low`: `#f1f4f6`
- `surface-container-lowest`: `#ffffff`
- `surface-container-high`: `#e3e9ec`
- `surface-container-highest`: `#dbe4e7`
- `outline-variant`: `#abb3b7` (fallback only; low alpha)
- `inverse-surface` (log panel): `#0c0f10`
- `inverse-on-surface` (log text): `#9b9d9e`

Typography:

- Headlines/display: `Manrope` (fallback to system sans)
- Body/labels: `Inter` (fallback to system sans)
- Technical log: monospaced stack for column stability

### 4.4 Stitch-Like Structural Layout (adapted to desktop app)

- Left rail:
  - compact product identity block
  - navigation entries (`Repair`, `Gemini Prompt`)
  - primary run CTA in persistent, high-salience location
- Main canvas:
  - compact top bar (title + utility actions)
  - card-based content area
  - result/log composition with dark technical log panel
- Bottom compact status strip:
  - live token/cost/status line, always visible and non-overlapping

### 4.5 Component Rules (from `DESIGN.md`)

- Cards:
  - no internal divider lines
  - separate header/content/footer by spacing and tonal steps
- Buttons:
  - primary CTA uses 135deg gradient (`primary` -> `primary_dim`)
  - secondary uses muted container tone (no hard borders)
- Inputs:
  - default inset surface (`surface-container-high`)
  - focus shifts to `surface-container-lowest` + soft ghost focus ring
- Log:
  - dark inverse container with timestamp/result highlighting
  - concise, technical, monospaced rendering

### 4.6 Settings Simplification

- Keep service cleanup as **one checkbox** with help popup:
  - Removes service tags/markup and configured `%...%` placeholders.
  - Repairs boundary spaces to prevent glued words.
- Keep stages independent so user can run split-only or cleanup-only.

## 5. Runtime Flow and State Model

### 5.1 Flow

1. User edits settings and prompt.
2. `RunController` validates inputs and stage selection.
3. Plan worker starts (`phase=plan`), no writes.
4. Review dialog opens with proposals.
5. If accepted, apply worker starts (`phase=apply`) with accepted proposal IDs.
6. Output and reports written.
7. UI shows per-file summary and final batch totals.

### 5.2 State Ownership

- Form values are read/written through a single `ViewState`.
- Panels publish change signals; controller updates state.
- Controller drives run-state transitions (`idle`, `planning`, `reviewing`, `applying`, `done`, `failed`).

## 6. Logging and Telemetry Behavior

GUI log should remain concise and human-readable:

- Batch start with settings summary.
- Per file:
  - plan started/finished and candidate counts
  - apply started/finished and key stats
- Final batch totals and output/report paths.

Detailed diagnostics remain in file log (`tmx-repair.log`), not in verbose GUI stream.

Token/cost counters remain visible and update frequently from progress events, but with stable line formats.

## 7. Error Handling and Reliability

- Centralized error routing in controller:
  - validation errors -> blocking dialog
  - worker errors -> status + concise GUI log + dialog
- Guard against null/missing config handoff between plan/apply.
- Keep path normalization for drag-drop and `file://` style URIs before existence checks.
- Preserve existing worker try/catch crash handling with traceback to log channel.

## 8. Migration Strategy (Low Risk)

### Phase 1: Structural extraction (no behavior changes)

- Move existing UI section build code into panel widgets.
- Keep old signal wiring, then route through controller facades.

### Phase 2: Controller ownership

- Introduce `RunController` and move `_run_repair` orchestration logic out of `MainWindow`.
- Keep same `RepairRunConfig` and worker contracts.

### Phase 3: Layout and style cleanup

- Replace fragile row sizing and splitter defaults.
- Tune spacing and adaptive behavior for smaller windows.
- Apply all `DESIGN.md` tokens and component constraints to Qt styles/widgets.
- Introduce rail + canvas + dark-log composition while preserving behavior.

### Phase 4: Final consistency and regression checks

- Verify split-only, cleanup-only, full run, dry run, and Gemini on/off scenarios.

## 9. Testing Strategy

### Automated

- Extend GUI flow tests for:
  - config collection by panel composition
  - plan/apply controller transitions
  - failure path rendering without null config regressions

### Manual smoke

- Window resize checks (small and medium sizes).
- Drag-drop local path and `file://` path behavior.
- Prompt edit in Prompt tab then run: verify edited prompt is used and logged.
- Batch progress readability with multiple TMX files.

## 10. Acceptance Criteria

- No control overlap/clipping at reduced window dimensions.
- Visual language matches `DESIGN.md` (tonal sculpting, no-line sections, editorial hierarchy).
- Settings are grouped and scannable; user can run split-only/cleanup-only clearly.
- Plan/review/apply flow behavior remains correct.
- GUI logging is concise (batch/file level), file logging stays detailed.
- Edited prompt from GUI is the one actually used at runtime and indicated in logs.
- No regressions in output/report generation paths for single and multi-file runs.

## 11. Risks and Mitigations

- **Risk:** behavior drift during extraction from `MainWindow`.
  - **Mitigation:** extract first without logic changes; then move orchestration.
- **Risk:** broken signal wiring across panels.
  - **Mitigation:** define explicit panel interfaces and add flow tests.
- **Risk:** accidental UX regressions in review flow.
  - **Mitigation:** keep `review_view.py` contract unchanged in first refactor pass.

## 12. Implementation Readiness

Design is ready to convert into a step-by-step implementation plan with concrete tasks and file-level checkpoints.
