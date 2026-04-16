"""TMX parser into in-memory dataclasses."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from core.models import TmxDocument, TranslationUnit


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def parse_tmx(path: Path) -> TmxDocument:
    tree = ET.parse(path)
    root = tree.getroot()
    header = root.find("header")
    body = root.find("body")
    if header is None or body is None:
        raise ValueError("Invalid TMX: missing header or body.")

    src_lang = header.attrib.get("srclang", "")
    units: list[TranslationUnit] = []
    tgt_lang_fallback = ""

    for index, tu in enumerate(body.findall("tu")):
        props: list[tuple[str, str]] = []
        segments: dict[str, str] = {}
        tuv_attribs: dict[str, dict[str, str]] = {}

        for child in list(tu):
            if child.tag == "prop":
                props.append((child.attrib.get("type", ""), child.text or ""))
                continue
            if child.tag != "tuv":
                continue

            lang = child.attrib.get(XML_LANG) or child.attrib.get("lang") or ""
            if lang and lang != src_lang and not tgt_lang_fallback:
                tgt_lang_fallback = lang

            seg = child.find("seg")
            segments[lang] = "".join(seg.itertext()) if seg is not None else ""
            tuv_attribs[lang] = dict(child.attrib)

        units.append(
            TranslationUnit(
                index=index,
                attribs=dict(tu.attrib),
                props=props,
                segments=segments,
                tuv_attribs=tuv_attribs,
                raw_element=tu,
            )
        )

    return TmxDocument(
        path=path,
        src_lang=src_lang,
        tgt_lang=tgt_lang_fallback,
        units=units,
        raw_tree=tree,
    )
