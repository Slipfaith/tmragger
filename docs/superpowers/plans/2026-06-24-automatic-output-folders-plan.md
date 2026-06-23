# Automatic Output Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove desktop output-path controls and route every generated file to an `output` folder beside its own input.

**Architecture:** Add one pure resolver in `core/output_paths.py`. Repair, conversion, cleanup, and Excel workers call it per input file; explicit CLI overrides remain authoritative.

**Tech Stack:** Python 3.11+, PySide6, pytest.

---

### Task 1: Shared resolver and Repair paths

**Files:**
- Create: `core/output_paths.py`
- Modify: `main.py`
- Modify: `ui/worker.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_output_paths.py`
- Test: `tests/test_main_paths.py`
- Test: `tests/test_worker_progress.py`

- [ ] Write failing tests for `sibling_output_dir(Path("a/file.tmx")) == Path("a/output")`, default CLI repaired/report paths, and RepairWorker automatic paths.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests\test_output_paths.py tests\test_main_paths.py tests\test_worker_progress.py -q -p no:cacheprovider`; verify failures are caused by old defaults.
- [ ] Add:

```python
from pathlib import Path

OUTPUT_DIRECTORY_NAME = "output"


def sibling_output_dir(input_path: Path) -> Path:
    return input_path.parent / OUTPUT_DIRECTORY_NAME
```

- [ ] Use the resolver only when no explicit CLI/config override is supplied. In GUI config pass `None` for output and report directories.
- [ ] Rerun focused tests and commit `Route Repair artifacts to sibling output folders`.

### Task 2: Converter, cleaner, and Excel workers

**Files:**
- Modify: `tmx2csv_app/gui.py`
- Modify: `tmx2csv_app/excel_to_tmx.py`
- Test: `tests/test_converter_output_paths.py`
- Test: `tests/test_edge_cases.py`

- [ ] Write failing tests with inputs in two distinct parent directories. Assert each workflow writes beneath its own `<parent>/output`.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests\test_converter_output_paths.py tests\test_edge_cases.py -q -p no:cacheprovider`; verify RED.
- [ ] In each worker loop compute `output_dir = sibling_output_dir(file_path)` immediately before processing. Make Excel create that directory and write `<stem>.tmx` there.
- [ ] Rerun focused tests and commit `Use per-file output folders in converter workflows`.

### Task 3: Remove manual output controls

**Files:**
- Modify: `ui/widgets/files_panel.py`
- Modify: `ui/main_window.py`
- Modify: `tmx2csv_app/gui.py`
- Test: `tests/test_files_panel.py`
- Test: `tests/test_converter_ui_text.py`
- Test: `tests/test_view_state_mapping.py`

- [ ] Write failing UI tests asserting Repair, Convert, and Clean contain no `output_edit` or `browse_output_button`, and GUI view state returns `output_dir=None`.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests\test_files_panel.py tests\test_converter_ui_text.py tests\test_view_state_mapping.py -q -p no:cacheprovider`; verify RED.
- [ ] Remove the output rows, browse methods, and run-time reads. Add Russian explanatory copy: `Результаты сохраняются в папку output рядом с каждым исходным файлом.`
- [ ] Rerun focused tests and commit `Remove manual output directory controls`.

### Task 4: Verification

**Files:**
- Do not modify: `asset/**`

- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --ignore=tests\test_offline_package.py`; require zero failures.
- [ ] Run the full suite and confirm only the three known missing-fixture failures in `test_offline_package.py`.
- [ ] Run `.\.venv\Scripts\python.exe -m compileall -q main.py core ui tmx2csv_app`.
- [ ] Run `git diff --check` and `git diff 931002c -- asset`; require no relevant output.
- [ ] Commit final corrections if any.
