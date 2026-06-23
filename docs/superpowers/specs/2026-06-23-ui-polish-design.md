# UI Polish Design

## Goal

Polish the existing PySide6 interface without restructuring the application or changing its behavior. The result must have consistent interaction sizes, states, surfaces, language, and subtle motion while preserving all current workflows.

## Constraints

- Use Python 3.11+ and existing PySide6 dependencies.
- Keep the current application architecture and navigation structure.
- Make minimal, focused changes.
- Preserve drag and drop, repair, conversion, cleanup, review, logging, and Gemini functionality.
- Use Russian for user-facing interface text. Established technical terms such as TMX, XLSX, JSON, Gemini, API and token units may remain unchanged.
- Do not modify any file under `asset/`.
- Do not add a new icon or animation dependency.
- Do not block the UI thread.

## Selected Approach

Use centralized QSS for shared visual rules and a small number of focused Qt components for behavior that QSS cannot provide. This keeps visual policy in `ui/theme.py`, while keyboard handling, drag state, surface effects, and page transitions remain isolated and testable.

The rejected alternatives are:

- QSS-only: smaller diff, but it cannot provide keyboard activation for the drop zone, explicit drag state, or interruptible page transitions.
- Full custom control library: gives maximum control, but would unnecessarily replace working widgets and increase regression risk.

## Components

### Theme and interaction states

`ui/theme.py` remains the source of shared colors, radii, control sizes, and widget states.

- Set the minimum interactive control height to 40 px for buttons, line edits, combo boxes, spin boxes, and checkboxes.
- Keep navigation buttons at their existing 52 x 52 px size.
- Increase transport buttons to at least 44 x 40 px.
- Give compact help buttons a 40 x 40 px hit area while retaining their current 14 x 14 px icon.
- Add explicit focus and disabled states for buttons and form fields.
- Retain Qt-native pressed feedback and add a subtle one-pixel visual inset where QSS supports it. Do not emulate CSS scale by resizing widget geometry.
- Define consistent radius values for major surfaces, groups, controls, and menus. Nested surfaces should use visually concentric radii when their spacing makes them read as one component.

### Drop zone

`ui/drop_zone.py` becomes the single behavioral implementation for the repair drop zone.

- Remove its private inline stylesheet and style `#dropZone` through `ui/theme.py`.
- Preserve mouse click and file drag-and-drop behavior.
- Add `StrongFocus`, an accessible name, and activation with Enter, Return, or Space.
- Maintain a `dragActive` dynamic property during valid drag enter/move/leave/drop events.
- Re-polish the widget when the dynamic property changes so drag feedback updates immediately.
- Ignore malformed or unsupported drops exactly as the current implementation does.

The converter drop areas in `tmx2csv_app/gui.py` retain their existing behavior but use the same theme vocabulary and Russian text.

### Surface depth

QSS does not support `box-shadow`, so a small helper will apply `QGraphicsDropShadowEffect` only to major card-like surfaces. The helper will use a low-opacity black shadow with small blur and offset values and will not be applied to dense list rows, inputs, or every group box.

Target surfaces are the top bar, primary canvas cards, and major status/settings cards where the effect is not clipped. If a surface clips or renders poorly under the offscreen test platform, it will retain the shared low-contrast outline instead of receiving an effect.

### Page transitions

A focused stacked-widget component will own navigation fades.

- The first page render is not animated.
- User-initiated page changes use a short opacity transition of 120-160 ms.
- A new selection during an active transition stops the previous animation and continues toward the latest state.
- Page contents, layout geometry, and navigation behavior remain unchanged.
- The effect is disabled when animation cannot be initialized safely; navigation must always complete.

### Dynamic numeric labels

`ui/widgets/status_panel.py` will give changing token, rate, cost, progress, and elapsed values stable presentation. Qt stylesheets do not expose reliable OpenType tabular-number control, so numeric status labels will use a fixed-pitch Qt font derived from the application font size and stable alignment. Static body copy remains in Inter.

### Russian interface text

Translate user-facing static labels, tooltips, navigation titles, review controls, initial status messages, converter controls, and completion-dialog buttons in:

- `ui/main_window.py`
- `ui/review_view.py`
- `ui/widgets/status_panel.py`
- `ui/widgets/gemini_settings_dialog.py`
- `tmx2csv_app/gui.py`

Do not translate file formats, model names, API names, paths, generated report content, raw log payloads, exception text from dependencies, or serialized values used by application logic.

## Data and Event Flow

The UI changes do not alter application data flow. Existing signals remain the public interaction mechanism:

- Drop-zone click and drop actions continue to emit `clicked` and `files_dropped`.
- Navigation buttons continue to select the same `QStackedWidget` indices.
- Repair and converter workers continue to own background processing.
- Status setters continue receiving the same values; only their visual formatting changes.

Dynamic styling is driven only by widget properties and Qt events. No new global state or persistence keys are introduced.

## Error Handling and Accessibility

- Invalid drops remain ignored without raising UI exceptions.
- Animation failures must fall back to immediate page selection.
- Focus indicators must remain visible against both white and tinted surfaces.
- Disabled controls must be visually distinct while keeping readable contrast.
- Keyboard activation must not emit duplicate clicks.
- Existing accessible names are preserved; the drop zone receives one.

## Testing

Implementation follows test-driven development.

- Extend theme tests to assert the 40 px minimum sizes and presence of focus, disabled, drop-active, and shared radius rules.
- Add drop-zone tests for keyboard activation, drag property transitions, supported TMX filtering, and malformed input handling.
- Extend shell tests for Russian navigation labels, minimum transport sizes, unchanged navigation targets, no initial animation, and interruptible subsequent transitions.
- Extend status-panel tests for stable numeric font/alignment and Russian labels.
- Extend review and converter tests for translated visible controls without changing their actions.
- Run focused tests after every red-green cycle, then run the complete test suite.
- Generate an offscreen screenshot for visual verification.
- Confirm `git diff -- asset` is empty before completion.

## Acceptance Criteria

- Every visible interactive control has at least a 40 px hit area unless a platform-owned subcontrol cannot be enlarged independently.
- Mouse, keyboard, and drag interactions work as specified.
- Focus and disabled states are visible and consistent.
- Drop zones use the central theme and show active drag feedback.
- Major surfaces have restrained, consistent depth without clipping.
- Page navigation uses a subtle interruptible fade and does not animate on startup.
- Dynamic numeric status text does not visibly shift as values change.
- User-facing interface text is Russian within the defined scope.
- Existing functional tests and the full suite pass.
- No file under `asset/` changes.
