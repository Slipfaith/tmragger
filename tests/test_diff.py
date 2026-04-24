from core.diff import render_inline_diff


def test_render_inline_diff_truncates_very_large_text():
    html = render_inline_diff(
        "A" * 50_000,
        "A" * 49_999 + "B",
        max_chars=1_000,
    )

    assert "Diff preview truncated" in html
    assert len(html) < 5_000
