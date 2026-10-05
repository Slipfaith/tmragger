from core.verification import VerificationRequest, render_prompt_template


def test_render_prompt_template_replaces_known_placeholders():
    req = VerificationRequest(
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
    req = VerificationRequest(
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
