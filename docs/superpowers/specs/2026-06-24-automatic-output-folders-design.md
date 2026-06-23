# Automatic Output Folders Design

## Goal

Remove manual output-directory controls from the desktop interface and save every generated artifact into an `output` directory beside its own input file.

## Selected Approach

Use one shared path resolver for all processing workflows:

```text
<input parent>/output
```

Each input path is resolved independently. A batch containing files from different directories therefore writes to a different sibling `output` directory for each source. This avoids coupling a batch to the first selected file and avoids displaying absolute local paths in the interface.

Rejected alternatives:

- One output directory beside the first input: incorrect for mixed-directory batches.
- Keep an optional manual override in the GUI: preserves the unnecessary path controls the change is intended to remove.
- Different folder names per tab: harder to predict and inconsistent across workflows.

## Shared Path Component

Create `core/output_paths.py` with:

- `OUTPUT_DIRECTORY_NAME = "output"`
- `sibling_output_dir(input_path: Path) -> Path`

The function is pure and returns `input_path.parent / OUTPUT_DIRECTORY_NAME`. The processing layer that writes a file remains responsible for creating the directory. Explicit CLI file and directory arguments continue to take precedence over the automatic path.

## Workflow Behavior

### Repair

For `C:/project/source.tmx`, automatic GUI and default CLI paths are:

- repaired TMX: `C:/project/output/source_repaired.tmx`
- XLSX report: `C:/project/output/source.diff-report.xlsx`
- Gemini JSON report, when enabled: `C:/project/output/source.verification.json`
- resume state: `C:/project/output/source.resume.json`
- shared Gemini cache for inputs in that source directory: `C:/project/output/gemini-cache.json`

`RepairWorker` resolves paths independently for every input. `MainWindow` supplies no GUI output/report override. Explicit CLI `--output`, `--output-dir`, `--report-file`, `--report-dir`, `--xlsx-report-file`, and `--xlsx-report-dir` values preserve their existing precedence.

### TMX conversion

`ConversionWorker` derives the output directory inside its per-file loop. CSV, XLSX, and split-TMX files for each input are written to that input's sibling `output` directory. Inputs from different directories do not share an output root.

### CSV/XLSX cleanup

Preview remains read-only. Clean mode derives the sibling `output` directory for each input before calling `clean_pair_file`. Existing cleaned filename rules are unchanged.

### Excel to TMX

`convert_excel_to_tmx` writes `<input stem>.tmx` inside the sibling `output` directory instead of next to the workbook. Existing column mapping and TMX content are unchanged.

## UI Changes

Remove output path fields and browse buttons from:

- the Repair files panel;
- the Convert tab;
- the Clean tab.

Excel to TMX already has no output path field. Add concise explanatory text to processing tabs stating that results are saved in an `output` directory beside each source file.

GUI state keeps `output_dir` only where required for backward-compatible data structures, but the desktop UI always supplies `None` and does not persist or display a path.

## Error Handling

Directory creation occurs immediately before output writing. An inaccessible source directory or output directory produces an error associated with that input file through the workflow's existing error channel. Batch workers continue processing other files according to their current per-file exception handling.

No source file is modified. Excel to TMX no longer risks overwriting an adjacent TMX file because its result is placed under `output`.

## Testing

Implementation follows test-driven development.

- Unit-test the shared resolver with relative, absolute, and differently located inputs.
- Verify RepairWorker produces automatic output, report, resume, and cache paths under the sibling `output` directory.
- Verify explicit CLI overrides retain precedence and default CLI paths use `output`.
- Verify Convert and Clean workers resolve output independently for files in different directories.
- Verify Excel to TMX creates its result under `output`.
- Verify the Repair, Convert, and Clean UIs contain no output path field or browse button.
- Run all executable tests with a writable temporary directory.
- Confirm the three existing offline-package failures remain attributable only to the missing `sample/Eventum Premo_En-Ru.tmx` fixture.
- Confirm `git diff -- asset` is empty.

## Acceptance Criteria

- No desktop tab displays or accepts an output directory.
- Every input file writes generated artifacts under `<input parent>/output`.
- Mixed-directory batches use the correct output directory for each input.
- Existing output filenames and formats remain unchanged except Excel to TMX moves into `output`.
- Explicit CLI overrides continue to work.
- Source files and `asset/` files remain unchanged.
