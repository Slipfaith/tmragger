from core.tm_cleanup import CleanupOptions, analyze_and_clean_segments


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


def test_remove_inline_tags_repairs_sentence_boundary_spacing():
    src = "One!<ph x=\"1\" type=\"0\"/>Two."
    tgt = "Раз!<ph x=\"1\" type=\"0\"/>Два."
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_inline_tags=True,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == "One! Two."
    assert result.tgt_inner_xml == "Раз! Два."
    assert any(action["rule"] == "remove_inline_tags" for action in result.auto_actions)


def test_remove_inline_tags_does_not_insert_space_inside_word():
    src = "co<ph x=\"1\"/>operate"
    tgt = "ко<ph x=\"1\"/>операция"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_inline_tags=True,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == "cooperate"
    assert result.tgt_inner_xml == "кооперация"


def test_remove_inline_tags_preserves_xml_safety_for_ampersand():
    src = "Rock &amp; Roll<ph x=\"1\"/>Night"
    tgt = "Рок &amp; Ролл<ph x=\"1\"/>Ночь"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_inline_tags=True,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == "Rock &amp; Roll Night"
    assert result.tgt_inner_xml == "Рок &amp; Ролл Ночь"
    assert result.src_plain_text == "Rock & Roll Night"
    assert result.tgt_plain_text == "Рок & Ролл Ночь"


def test_remove_inline_tags_inserts_separator_between_lower_and_upper_words():
    src = "Title<ph x=\"1\"/>Body"
    tgt = "Заголовок<ph x=\"1\"/>Текст"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_inline_tags=True,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == "Title Body"
    assert result.tgt_inner_xml == "Заголовок Текст"

