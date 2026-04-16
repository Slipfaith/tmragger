# TMX Repair + PySide6 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-pass TMX repair tool that safely splits bilingual TU segments by sentence, with a PySide6 drag-and-drop app, logging, and a separate Gemini verification prompt tab.

**Architecture:** Core logic is isolated in `core/*` (parse, split, repair, save), while UI is in `ui/*`. The splitter is conservative: only apply split when source/target sentence counts match and each part is non-empty. The UI orchestrates core services and writes logs both to screen and optional file.

**Tech Stack:** Python 3.13, xml.etree.ElementTree, dataclasses, pytest, PySide6, standard logging.

---

### Task 1: Test sentence splitting behavior

**Files:**
- Create: `tests/test_sentence_splitter.py`
- Test: `tests/test_sentence_splitter.py`

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Run tests and verify they fail**
- [ ] **Step 3: Implement minimal splitter logic**
- [ ] **Step 4: Run tests and verify they pass**

### Task 2: Test repair pipeline on miniature TMX

**Files:**
- Create: `tests/test_repair_pipeline.py`
- Test: `tests/test_repair_pipeline.py`

- [ ] **Step 1: Write failing integration test**
- [ ] **Step 2: Run test and verify failure**
- [ ] **Step 3: Implement parser/repair/writer core modules**
- [ ] **Step 4: Run test and verify pass**

### Task 3: Build PySide6 GUI (drag-drop + logging + Gemini tab)

**Files:**
- Create: `ui/main_window.py`
- Create: `ui/logging_utils.py`
- Modify: `main.py`

- [ ] **Step 1: Add main window with drag-and-drop TMX input**
- [ ] **Step 2: Add log panel + file logging hooks**
- [ ] **Step 3: Add separate Gemini prompt tab and copy button**
- [ ] **Step 4: Wire repair action to core logic and show stats**

### Task 4: Final verification

**Files:**
- Modify: `requirements.txt` (if missing)

- [ ] **Step 1: Run full test suite**
- [ ] **Step 2: Run syntax compile check**
- [ ] **Step 3: Record known limits in summary**
