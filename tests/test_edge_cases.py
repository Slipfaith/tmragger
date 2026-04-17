"""Stage 4 edge-case tests: BOM, CRLF, CDATA, XML entities, multilang.

These cover inputs the pipeline is likely to encounter from real-world
CAT tools (SDL, memoQ, Phrase) and verify that nothing blows up on
them and that content is preserved byte-for-byte where expected.
"""

from __future__ import annotations

from pathlib import Path

from core.repair import repair_tmx_file


RUNTIME_DIR = Path("tests") / "fixtures" / "runtime"


def _prepare() -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return RUNTIME_DIR


def _single_tu_tmx() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<tmx version="1.4">\n'
        '  <header srclang="en-US" adminlang="en-US" '
        'creationtool="test" creationtoolversion="1.0" datatype="xml"/>\n'
        '  <body>\n'
        '    <tu creationid="u1">\n'
        '      <tuv xml:lang="en-US"><seg>Hello world.</seg></tuv>\n'
        '      <tuv xml:lang="ru-RU"><seg>Privet mir.</seg></tuv>\n'
        '    </tu>\n'
        '  </body>\n'
        '</tmx>\n'
    )


# ---------------------------------------------------------------------- BOM

def test_bom_prefixed_utf8_input_is_parseable_and_roundtrips():
    runtime = _prepare()
    inp = runtime / "bom_in.tmx"
    out = runtime / "bom_out.tmx"
    # write UTF-8 with an explicit BOM — some Windows exports include one
    inp.write_bytes(b"\xef\xbb\xbf" + _single_tu_tmx().encode("utf-8"))

    stats = repair_tmx_file(input_path=inp, output_path=out, dry_run=False)

    assert out.exists(), "pipeline must produce an output file even for BOM input"
    assert stats.total_tus == 1
    out_text = out.read_text(encoding="utf-8-sig")
    assert "Hello world." in out_text
    assert "Privet mir." in out_text

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


# --------------------------------------------------------------------- CRLF

def test_crlf_line_endings_do_not_break_parser():
    runtime = _prepare()
    inp = runtime / "crlf_in.tmx"
    out = runtime / "crlf_out.tmx"
    inp.write_bytes(_single_tu_tmx().replace("\n", "\r\n").encode("utf-8"))

    stats = repair_tmx_file(input_path=inp, output_path=out, dry_run=False)
    assert stats.total_tus == 1
    out_text = out.read_text(encoding="utf-8")
    assert "Hello world." in out_text

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


# ------------------------------------------------------------- XML entities

def test_xml_entities_in_seg_are_preserved_after_repair():
    """`&amp;`, `&lt;`, `&gt;` inside <seg> must not be double-escaped or lost."""
    runtime = _prepare()
    inp = runtime / "entities_in.tmx"
    out = runtime / "entities_out.tmx"
    inp.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<tmx version="1.4">\n'
        '  <header srclang="en-US" adminlang="en-US" '
        'creationtool="test" creationtoolversion="1.0" datatype="xml"/>\n'
        '  <body>\n'
        '    <tu creationid="u1">\n'
        '      <tuv xml:lang="en-US"><seg>Tom &amp; Jerry use 2 &lt; 3.</seg></tuv>\n'
        '      <tuv xml:lang="ru-RU"><seg>Tom i Jerry: 2 &lt; 3.</seg></tuv>\n'
        '    </tu>\n'
        '  </body>\n'
        '</tmx>\n',
        encoding="utf-8",
    )

    repair_tmx_file(input_path=inp, output_path=out, dry_run=False)
    out_text = out.read_text(encoding="utf-8")
    # Literal ampersand/angle bracket characters must stay escaped on output.
    assert "Tom &amp; Jerry" in out_text
    assert "2 &lt; 3" in out_text
    # And they must not show up double-escaped (&amp;amp;).
    assert "&amp;amp;" not in out_text
    assert "&amp;lt;" not in out_text

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


# ---------------------------------------------------------------- multilang

def test_multilang_tu_is_skipped_and_left_unchanged():
    """TUs with more than 2 <tuv> entries are unsafe to split bilaterally —
    the pipeline must leave them alone and log a warning instead of guessing."""
    runtime = _prepare()
    inp = runtime / "multilang_in.tmx"
    out = runtime / "multilang_out.tmx"
    inp.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<tmx version="1.4">\n'
        '  <header srclang="en-US" adminlang="en-US" '
        'creationtool="test" creationtoolversion="1.0" datatype="xml"/>\n'
        '  <body>\n'
        '    <tu creationid="u1">\n'
        '      <tuv xml:lang="en-US"><seg>Hello world. Next sentence.</seg></tuv>\n'
        '      <tuv xml:lang="ru-RU"><seg>Privet mir. Sleduiushchee.</seg></tuv>\n'
        '      <tuv xml:lang="de-DE"><seg>Hallo Welt. Naechster Satz.</seg></tuv>\n'
        '    </tu>\n'
        '  </body>\n'
        '</tmx>\n',
        encoding="utf-8",
    )

    stats = repair_tmx_file(input_path=inp, output_path=out, dry_run=False)
    assert stats.split_tus == 0, "multilang TU must not be split"
    assert stats.skipped_tus >= 1
    out_text = out.read_text(encoding="utf-8")
    # Original text preserved verbatim — all three segments intact.
    assert "Hello world. Next sentence." in out_text
    assert "Privet mir. Sleduiushchee." in out_text
    assert "Hallo Welt. Naechster Satz." in out_text

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


# --------------------------------------------------------------------- CDATA

def test_cdata_in_seg_preserved_as_text():
    """CDATA sections round-trip as equivalent text content. ElementTree
    normalises CDATA to regular text — we just need content preserved,
    not the literal `<![CDATA[...]]>` syntax."""
    runtime = _prepare()
    inp = runtime / "cdata_in.tmx"
    out = runtime / "cdata_out.tmx"
    inp.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<tmx version="1.4">\n'
        '  <header srclang="en-US" adminlang="en-US" '
        'creationtool="test" creationtoolversion="1.0" datatype="xml"/>\n'
        '  <body>\n'
        '    <tu creationid="u1">\n'
        '      <tuv xml:lang="en-US"><seg><![CDATA[Use A & B > C.]]></seg></tuv>\n'
        '      <tuv xml:lang="ru-RU"><seg><![CDATA[Ispolzuyte A i B.]]></seg></tuv>\n'
        '    </tu>\n'
        '  </body>\n'
        '</tmx>\n',
        encoding="utf-8",
    )

    stats = repair_tmx_file(input_path=inp, output_path=out, dry_run=False)
    assert stats.total_tus == 1
    out_text = out.read_text(encoding="utf-8")
    # Content survives — whether as CDATA or as escaped entities doesn't matter.
    assert "Use A" in out_text and "B" in out_text
    assert "Ispolzuyte A" in out_text
    # The `&` character must not appear unescaped in the output XML.
    # (It should be either inside a CDATA section or rendered as &amp;.)
    import re
    bad = re.search(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", out_text)
    assert bad is None, f"unescaped ampersand in output: ...{out_text[max(0,bad.start()-20):bad.start()+20]}..." if bad else ""

    inp.unlink(missing_ok=True)
    out.unlink(missing_ok=True)


# ------------------------------------------------------- output byte-safety

def test_output_can_be_re_parsed_by_same_pipeline():
    """Round-trip: repair output must itself be valid TMX that the pipeline
    can consume without crashing — a cheap proxy for 'we wrote valid XML'."""
    runtime = _prepare()
    inp = runtime / "roundtrip_in.tmx"
    out1 = runtime / "roundtrip_out1.tmx"
    out2 = runtime / "roundtrip_out2.tmx"
    inp.write_text(_single_tu_tmx(), encoding="utf-8")

    repair_tmx_file(input_path=inp, output_path=out1, dry_run=False)
    stats = repair_tmx_file(input_path=out1, output_path=out2, dry_run=False)
    assert stats.total_tus == 1

    for p in (inp, out1, out2):
        p.unlink(missing_ok=True)
