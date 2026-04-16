from core.tm_cleanup import analyze_and_clean_segments


def test_auto_normalize_ascii_spaces_only():
    src = "  Hello\u00A0\u202F  world\t\n"
    tgt = "  Привет\u00A0мир \n "
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
    )

    assert result.src_inner_xml == "Hello\u00A0\u202F world\t\n"
    assert result.tgt_inner_xml == "Привет\u00A0мир \n"
    assert result.remove_tu is False
    assert any(action["rule"] == "normalize_spaces" for action in result.auto_actions)


def test_auto_remove_when_target_has_no_letters_but_source_has_content():
    src = "Need translation now."
    tgt = "<ph id=\"1\"/> !!!"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
    )

    assert result.remove_tu is True
    assert result.remove_reason == "target_missing_letters"


def test_warn_identical_source_target_for_different_languages():
    src = "Save changes now"
    tgt = "Save changes now"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
    )

    assert result.remove_tu is False
    assert any(warn["rule"] == "identical_source_target" for warn in result.warnings)


def test_warn_length_anomaly():
    src = "This is a fairly long source segment with many words."
    tgt = "Коротко."
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
    )

    assert any(warn["rule"] == "length_anomaly" for warn in result.warnings)


def test_warn_obvious_lang_mismatch():
    src = "Это русский текст"
    tgt = "This is english text"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
    )

    rules = {warn["rule"] for warn in result.warnings}
    assert "lang_mismatch_source" in rules
    assert "lang_mismatch_target" in rules
