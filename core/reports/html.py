"""HTML diff report generator.

Produces a self-contained, tabbed HTML document summarising what the
pipeline did: splits, auto-cleanup edits, warnings, and Gemini audit
results. The template is a single f-string (no external deps) so the
file is browsable as-is from Windows Explorer.

``RepairStats`` is only used for its attributes, so we type-hint it as
a forward reference to avoid a runtime import cycle with ``core.repair``.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from core.diff import render_inline_diff

if TYPE_CHECKING:  # pragma: no cover
    from core.repair import RepairStats


def write_html_diff_report(
    path: Path,
    input_path: Path,
    output_path: Path,
    stats: "RepairStats",
    split_events: list[dict[str, object]],
    cleanup_events: list[dict[str, object]],
    warning_events: list[dict[str, object]],
    gemini_audit_events: list[dict[str, object]],
) -> None:
    split_blocks: list[str] = []
    for event in split_events:
        src_parts_html = "".join(f"<li>{escape(part)}</li>" for part in event["src_parts"])
        tgt_parts_html = "".join(f"<li>{escape(part)}</li>" for part in event["tgt_parts"])
        confidence = escape(str(event["confidence"]))
        gemini_verdict = event.get("gemini_verdict")
        gemini_badge = ""
        if gemini_verdict is not None:
            gemini_badge = f'<span class="badge">Gemini: {escape(str(gemini_verdict))}</span>'

        split_blocks.append(
            f"""
            <section class="card">
              <h2>TU #{int(event["tu_index"]) + 1}</h2>
              <p><span class="badge">Confidence: {confidence}</span>{gemini_badge}</p>
              <div class="grid">
                <div class="pane">
                  <h3>Source Before</h3>
                  <pre>{escape(str(event["original_src"]))}</pre>
                  <h3>Source After</h3>
                  <ol>{src_parts_html}</ol>
                </div>
                <div class="pane">
                  <h3>Target Before</h3>
                  <pre>{escape(str(event["original_tgt"]))}</pre>
                  <h3>Target After</h3>
                  <ol>{tgt_parts_html}</ol>
                </div>
              </div>
            </section>
            """
        )

    if not split_blocks:
        split_blocks.append(
            '<section class="card"><h2>No Split Changes</h2>'
            "<p>No TU entries were split in this run.</p></section>"
        )

    cleanup_blocks: list[str] = []
    for event in cleanup_events:
        before_src = str(event.get("before_src", ""))
        after_src = str(event.get("after_src", ""))
        before_tgt = str(event.get("before_tgt", ""))
        after_tgt = str(event.get("after_tgt", ""))
        src_diff_html = render_inline_diff(before_src, after_src)
        tgt_diff_html = render_inline_diff(before_tgt, after_tgt)
        cleanup_blocks.append(
            f"""
            <section class="card">
              <h2>TU #{int(event.get("tu_index", 0)) + 1}</h2>
              <p><span class="badge auto">AUTO: {escape(str(event.get("rule", "")))}</span></p>
              <p>{escape(str(event.get("message", "")))}</p>
              <div class="grid">
                <div class="pane">
                  <h3>Source Diff</h3>
                  {src_diff_html}
                </div>
                <div class="pane">
                  <h3>Target Diff</h3>
                  {tgt_diff_html}
                </div>
              </div>
            </section>
            """
        )
    if not cleanup_blocks:
        cleanup_blocks.append(
            '<section class="card"><h2>No Auto Cleanup Actions</h2>'
            "<p>No AUTO cleanup actions were applied in this run.</p></section>"
        )

    warning_blocks: list[str] = []
    for event in warning_events:
        warning_blocks.append(
            f"""
            <section class="card">
              <h2>TU #{int(event.get("tu_index", 0)) + 1}</h2>
              <p><span class="badge warn">{escape(str(event.get("rule", "")))}</span></p>
              <p>{escape(str(event.get("message", "")))}</p>
              <div class="grid">
                <div class="pane">
                  <h3>Source Snapshot</h3>
                  <pre>{escape(str(event.get("src_text", "")))}</pre>
                </div>
                <div class="pane">
                  <h3>Target Snapshot</h3>
                  <pre>{escape(str(event.get("tgt_text", "")))}</pre>
                </div>
              </div>
            </section>
            """
        )
    if not warning_blocks:
        warning_blocks.append(
            '<section class="card"><h2>No Warnings</h2>'
            "<p>No WARN diagnostics were produced in this run.</p></section>"
        )

    gemini_blocks: list[str] = []
    for event in gemini_audit_events:
        kind = escape(str(event.get("kind", "unknown")))
        verdict = escape(str(event.get("verdict", "n/a")))
        summary = escape(str(event.get("summary", "")))
        issues_count = int(event.get("issues_count", 0) or 0)
        gemini_blocks.append(
            f"""
            <section class="card">
              <h2>TU #{int(event.get("tu_index", 0)) + 1}</h2>
              <p><span class="badge">Gemini {kind}</span><span class="badge">{verdict}</span></p>
              <p>Issues: {issues_count}</p>
              <p>{summary}</p>
            </section>
            """
        )
    if not gemini_blocks:
        gemini_blocks.append(
            '<section class="card"><h2>No Gemini Checks</h2>'
            "<p>Gemini verification was not used for this run.</p></section>"
        )

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TMX Repair Diff Report</title>
  <style>
    body {{ font-family: Segoe UI, Tahoma, Arial, sans-serif; background: #f5f7fa; color: #1f2937; margin: 0; }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
    .hero {{ background: linear-gradient(135deg, #0f766e, #2563eb); color: #fff; border-radius: 12px; padding: 18px 20px; }}
    .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; margin-top: 10px; }}
    .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; margin-top: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .pane {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px; }}
    .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }}
    .tab-button {{ border: 1px solid #cbd5e1; border-radius: 10px; background: #fff; padding: 8px 12px; cursor: pointer; font-weight: 600; }}
    .tab-button.active {{ background: #0f766e; color: #fff; border-color: #0f766e; }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .badge {{ display: inline-block; background: #e0f2fe; color: #075985; border-radius: 999px; padding: 4px 10px; margin-right: 8px; font-size: 12px; font-weight: 600; }}
    .badge.auto {{ background: #dcfce7; color: #166534; }}
    .badge.warn {{ background: #fef3c7; color: #92400e; }}
    .diff-wrap {{ display: grid; gap: 8px; }}
    .diff-line {{ background: #fff; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 8px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; font-family: Consolas, 'Courier New', monospace; font-size: 13px; }}
    .diff-label {{ font-weight: 700; margin-right: 6px; color: #334155; }}
    .diff-del {{ background: #fee2e2; color: #991b1b; border-radius: 4px; padding: 0 1px; }}
    .diff-add {{ background: #86efac; color: #14532d; border: 1px solid #15803d; border-radius: 4px; padding: 0 1px; font-weight: 700; }}
    .diff-eq {{ color: #1f2937; }}
    .after-line .diff-eq {{ color: #475569; }}
    .diff-note {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
    .ws {{ display: inline-block; min-width: 0.8em; text-align: center; border-radius: 3px; border: 1px solid #93c5fd; margin: 0 0.5px; font-size: 11px; line-height: 1.05; background: #dbeafe; color: #1d4ed8; }}
    .ws-space {{ background: #dbeafe; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 8px; }}
    ol {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 4px 0; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <h1>TMX Repair Diff Report</h1>
      <div class="meta">
        <div>Input: {escape(str(input_path))}</div>
        <div>Output: {escape(str(output_path))}</div>
        <div>Total TU: {stats.total_tus}</div>
        <div>Split TU: {stats.split_tus}</div>
        <div>Output TU: {stats.created_tus}</div>
        <div>Skipped TU: {stats.skipped_tus}</div>
        <div>High Confidence: {stats.high_confidence_splits}</div>
        <div>Medium Confidence: {stats.medium_confidence_splits}</div>
        <div>Gemini Checked: {stats.gemini_checked}</div>
        <div>Gemini Rejected: {stats.gemini_rejected}</div>
        <div>Gemini Tokens In: {stats.gemini_input_tokens}</div>
        <div>Gemini Tokens Out: {stats.gemini_output_tokens}</div>
        <div>Gemini Tokens Total: {stats.gemini_total_tokens}</div>
        <div>Gemini Est Cost (USD): {stats.gemini_estimated_cost_usd:.6f}</div>
        <div>AUTO Actions: {stats.auto_actions}</div>
        <div>AUTO Removed TU: {stats.auto_removed_tus}</div>
        <div>WARN Issues: {stats.warn_issues}</div>
      </div>
    </header>
    <div class="tabs">
      <button class="tab-button active" data-tab="split">Split Changes</button>
      <button class="tab-button" data-tab="cleanup">Auto Cleanup</button>
      <button class="tab-button" data-tab="warnings">Warnings</button>
      <button class="tab-button" data-tab="gemini">Gemini Checks</button>
    </div>
    <section class="tab-panel active" id="tab-split">{"".join(split_blocks)}</section>
    <section class="tab-panel" id="tab-cleanup">{"".join(cleanup_blocks)}</section>
    <section class="tab-panel" id="tab-warnings">{"".join(warning_blocks)}</section>
    <section class="tab-panel" id="tab-gemini">{"".join(gemini_blocks)}</section>
  </div>
  <script>
    (function() {{
      var buttons = document.querySelectorAll(".tab-button");
      var panels = document.querySelectorAll(".tab-panel");
      buttons.forEach(function(btn) {{
        btn.addEventListener("click", function() {{
          buttons.forEach(function(other) {{ other.classList.remove("active"); }});
          panels.forEach(function(panel) {{ panel.classList.remove("active"); }});
          btn.classList.add("active");
          var target = document.getElementById("tab-" + btn.getAttribute("data-tab"));
          if (target) {{
            target.classList.add("active");
          }}
        }});
      }});
    }})();
  </script>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")
