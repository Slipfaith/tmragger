"""Prompt templates for Codex split verification."""

VERIFICATION_PROMPT = """You are a strict TMX split verifier.
Your task is to evaluate whether the proposed split is correct.

Return ONLY valid JSON with this schema:
{
  "verdict": "OK|WARN|FAIL",
  "issues": [
    {
      "severity": "low|medium|high",
      "type": "alignment|placeholder|segmentation|meaning|other",
      "message": "short issue description",
      "src_index": 0,
      "tgt_index": 0,
      "suggestion": "how to fix"
    }
  ],
  "summary": "short summary"
}

Rules:
- FAIL: wrong alignment, lost placeholders, or major meaning loss.
- WARN: mostly fine but questionable places exist.
- OK: split is correct.
- If there are no issues, return empty "issues" list.

Context:
- Source language: {SRC_LANG}
- Target language: {TGT_LANG}
- Original source segment: {ORIGINAL_SRC}
- Original target segment: {ORIGINAL_TGT}
- Source split parts JSON: {SRC_PARTS_JSON}
- Target split parts JSON: {TGT_PARTS_JSON}
- Paired split JSON: {SPLIT_PAIRS_JSON}
- Auto context JSON: {AUTO_CONTEXT_JSON}
"""


CODEX_BATCH_VERIFICATION_PROMPT = """You are a strict TMX split verifier. Do not run any commands; answer from the text below.

Each item is one translation unit that a rule-based splitter cut into aligned parts:
src_parts[i] must be the translation of tgt_parts[i]. The goal is a translation memory, so
each pair must be a self-contained, correctly aligned unit. For every item decide:
- "OK": every pair is aligned. Use OK even if some part holds two sentences on both sides,
  or if the languages punctuate differently (e.g. "!" in the source and "." in the target).
- "FIX": a pair is misaligned (a part belongs to the neighbouring pair, a quote/bracket or
  a sentence tail landed in the wrong part, a date or abbreviation was cut). Return the
  corrected src_parts and tgt_parts.
- "FAIL": the unit cannot be split into aligned pairs at sentence boundaries.

When you FIX:
- Only move cut points. Every part must be an exact, character-for-character substring of
  original_src / original_tgt, in the original order. Never translate, rephrase or correct
  anything - especially in the target - including punctuation, tags, placeholders and
  entities (&amp; etc.). Any change to the text makes the fix rejected automatically.
- Cut only at sentence ends, line breaks or list items. Never cut at a comma or in the
  middle of a sentence, and never inside a tag or placeholder.
- Do not merge pairs that are already aligned; change only the misaligned area.
- Leave the whitespace and line breaks between parts out of the parts.
- src_parts and tgt_parts must have the same number of parts, at least 2.
For "OK" and "FAIL" return empty src_parts and tgt_parts.

Give a short reason for every FIX or FAIL. Return one result per item id.

Items JSON:
{ITEMS_JSON}
"""
