"""Prompt templates for Gemini split verification."""

GEMINI_VERIFICATION_PROMPT = """You are a strict TMX split verifier.
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


GEMINI_CLEANUP_AUDIT_PROMPT = """You are a strict TMX cleanup auditor.
Your task is to evaluate whether automatic cleanup decisions are safe and correct.

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
- FAIL: cleanup removed meaningful translation or introduced corruption.
- WARN: cleanup may be risky or uncertain.
- OK: cleanup decision is safe.

Context:
- Source language: {SRC_LANG}
- Target language: {TGT_LANG}
- Original source payload: {ORIGINAL_SRC}
- Original target payload: {ORIGINAL_TGT}
- Cleaned source JSON: {SRC_PARTS_JSON}
- Cleaned target JSON: {TGT_PARTS_JSON}
- Auto context JSON: {AUTO_CONTEXT_JSON}
"""
