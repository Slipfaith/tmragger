from core.gemini_client import GeminiVerificationRequest, parse_gemini_response, render_prompt_template


def test_render_prompt_template_replaces_known_placeholders():
    req = GeminiVerificationRequest(
        src_lang="en-US",
        tgt_lang="ru-RU",
        original_src="A. B.",
        original_tgt="A. B.",
        src_parts=["A.", "B."],
        tgt_parts=["A.", "B."],
    )
    template = "CHECK {SRC_LANG} -> {TGT_LANG} :: {ORIGINAL_SRC}"
    rendered = render_prompt_template(template, req)
    assert "CHECK en-US -> ru-RU :: A. B." in rendered
    assert "Auto context JSON:" in rendered


def test_render_prompt_template_uses_auto_context_placeholder():
    req = GeminiVerificationRequest(
        src_lang="en-US",
        tgt_lang="fr-FR",
        original_src="One. Two.",
        original_tgt="Un. Deux.",
        src_parts=["One.", "Two."],
        tgt_parts=["Un.", "Deux."],
    )
    template = "CTX\n{AUTO_CONTEXT_JSON}"
    rendered = render_prompt_template(template, req)
    assert "Auto context JSON:" not in rendered
    assert '"src_lang": "en-US"' in rendered
    assert '"tgt_lang": "fr-FR"' in rendered


def test_parse_gemini_response_extracts_usage_metadata():
    raw = """
    {
      "candidates": [
        {
          "content": {
            "parts": [
              {
                "text": "{\\"verdict\\": \\"OK\\", \\"issues\\": [], \\"summary\\": \\"ok\\"}"
              }
            ]
          }
        }
      ],
      "usageMetadata": {
        "promptTokenCount": 128,
        "candidatesTokenCount": 32,
        "totalTokenCount": 160
      }
    }
    """
    result = parse_gemini_response(raw)
    assert result.verdict == "OK"
    assert result.prompt_tokens == 128
    assert result.completion_tokens == 32
    assert result.total_tokens == 160
