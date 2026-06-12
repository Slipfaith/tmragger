"""Tests for Stage 0 fixes: empty-seg preservation, 1-based TU numbering,
prop filtering on splits, and multi-language TU guard."""

from __future__ import annotations

from pathlib import Path

from core.repair import repair_tmx_file


RUNTIME_DIR = Path("tests") / "fixtures" / "runtime"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _prepare() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def test_short_empty_elements_disabled_preserves_long_form_tags():
    """Empty elements like <header> must not be collapsed to `<header/>` on output.
    Some downstream CAT tools choke on self-closing tags, so we force long form."""
    runtime = _prepare()
    inp = runtime / "empty_seg_in.tmx"
    out = runtime / "empty_seg_out.tmx"
    inp.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>Hello world.</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Privet mir.</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    repair_tmx_file(input_path=inp, output_path=out, dry_run=False)

    content = _read(out)
    # Header was self-closing on input; after serialization with
    # short_empty_elements=False it must render as `<header ...></header>`.
    assert "</header>" in content
    assert "<header" in content
    # Generic sanity: no self-closing header/body tags survived.
    assert "<header/>" not in content
    assert "<header />" not in content

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


def test_split_drops_context_props_but_keeps_note():
    """Context-referencing <prop> entries must be dropped from split TUs;
    neutral ones (x-Note) must survive."""
    runtime = _prepare()
    inp = runtime / "prop_filter_in.tmx"
    out = runtime / "prop_filter_out.tmx"
    inp.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <prop type="x-Note">keepme</prop>
      <prop type="x-Context">dropme-ctx</prop>
      <prop type="x-ContextPre">dropme-pre</prop>
      <prop type="x-ContextPost">dropme-post</prop>
      <prop type="x-ContextContent">dropme-content</prop>
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Privet mir. Sleduiushchee predlozhenie!</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    repair_tmx_file(input_path=inp, output_path=out, dry_run=False)

    content = _read(out)
    # Split produced 2 TUs; each must preserve x-Note and drop context props.
    assert content.count("<tu ") == 2
    assert content.count("keepme") == 2
    assert "dropme-ctx" not in content
    assert "dropme-pre" not in content
    assert "dropme-post" not in content
    assert "dropme-content" not in content

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


def test_multilang_tu_is_left_unchanged_with_warning():
    """A TU with 3+ <tuv> must be skipped with a warning and copied verbatim."""
    runtime = _prepare()
    inp = runtime / "multilang_in.tmx"
    out = runtime / "multilang_out.tmx"
    inp.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<tmx version="1.4">
  <header srclang="en-US" adminlang="en-US" creationtool="test" creationtoolversion="1.0" datatype="xml"/>
  <body>
    <tu creationid="u1">
      <tuv xml:lang="en-US"><seg>Hello world. Next sentence!</seg></tuv>
      <tuv xml:lang="ru-RU"><seg>Privet mir. Sleduiushchee predlozhenie!</seg></tuv>
      <tuv xml:lang="de-DE"><seg>Hallo Welt. Naechster Satz!</seg></tuv>
    </tu>
  </body>
</tmx>
""",
        encoding="utf-8",
    )

    events: list[dict] = []
    stats = repair_tmx_file(
        input_path=inp,
        output_path=out,
        dry_run=False,
        progress_callback=lambda p: events.append(dict(p)),
    )

    # No split, TU preserved as-is, warning issued.
    assert stats.split_tus == 0
    assert stats.created_tus == 1
    assert stats.warn_issues >= 1

    content = _read(out)
    assert content.count("<tu ") == 1
    # All three languages preserved.
    assert 'xml:lang="en-US"' in content
    assert 'xml:lang="ru-RU"' in content
    assert 'xml:lang="de-DE"' in content

    # Warning event emitted to callback.
    reasons = {str(e.get("reason", "")) for e in events if e.get("event") == "tu_skipped"}
    assert "multilang_tu" in reasons

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
