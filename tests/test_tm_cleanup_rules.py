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


def test_auto_remove_when_source_equals_target_cross_language():
    src = "Save changes now"
    tgt = "Save changes now"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
    )

    assert result.remove_tu is True
    assert result.remove_reason == "source_equals_target"


def test_warn_identical_source_target_when_removal_disabled():
    src = "Save changes now"
    tgt = "Save changes now"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(remove_garbage_segments=False, emit_warnings=True),
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


def test_remove_inline_tags_removes_game_markup_and_keeps_text_readable():
    src = (
        "Прочность щита: ^{85 221 85}^%param%%^{/color}^ от максимального здоровья титана "
        "^{237 194 154}^(зависит от силы тотема)^{/color}^ Шанс срабатывания: "
        "^{255 255 255}^%scale%%^{/color}^ ^{237 194 154}^(зависит от ранга навыка)^{/color}^"
    )
    tgt = (
        "Shield durability: ^{85 221 85}^%param%%^{/color}^ of maximum Titan Health "
        "^{237 194 154}^(depends on Totem power)^{/color}^Activation chance: "
        "^{255 255 255}^%scale%%^{/color}^ ^{237 194 154}^(depends on skill rank)^{/color}^"
    )
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="ru-RU",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_inline_tags=True,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert "^{85 221 85}^" not in result.src_inner_xml
    assert "^{/color}^" not in result.src_inner_xml
    assert "^{85 221 85}^" not in result.tgt_inner_xml
    assert "^{/color}^" not in result.tgt_inner_xml
    assert "Activation chance" in result.tgt_inner_xml
    assert "Health (depends on Totem power) Activation chance" in result.tgt_inner_xml
    assert any(action["rule"] == "remove_game_markup" for action in result.auto_actions)


def test_remove_inline_tags_resolves_m_variants_to_first_form():
    src = "Потрать %param1% Руководств$m(о|а) и прокачай навыки"
    tgt = "Spend %param1% Manual$m(s|s) to upgrade skills"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="ru-RU",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_inline_tags=True,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == "Потрать %param1% Руководство и прокачай навыки"
    assert result.tgt_inner_xml == "Spend %param1% Manuals to upgrade skills"


def test_remove_game_markup_can_run_without_inline_tag_removal():
    src = "Прочность: ^{85 221 85}^%param%%^{/color}^"
    tgt = "Durability: ^{85 221 85}^%param%%^{/color}^"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="ru-RU",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_game_markup=True,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == "Прочность: %param%%"
    assert result.tgt_inner_xml == "Durability: %param%%"
    assert any(action["rule"] == "remove_game_markup" for action in result.auto_actions)


def test_remove_game_markup_can_be_disabled_independently():
    src = "Прочность: ^{85 221 85}^%param%%^{/color}^"
    tgt = "Durability: ^{85 221 85}^%param%%^{/color}^"
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="ru-RU",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_game_markup=False,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == src
    assert result.tgt_inner_xml == tgt
    assert all(action["rule"] != "remove_game_markup" for action in result.auto_actions)


def test_remove_percent_wrapped_tokens_removes_only_safe_placeholder_patterns():
    src = "Damage from movement: %paramFloor%% bonus, duration %scale% sec, crit 100%."
    tgt = "Урон: %paramFloor%% бонус, длительность %scale% сек, шанс 100%."
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_percent_wrapped_tokens=True,
            remove_game_markup=False,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert "%paramFloor%" not in result.src_inner_xml
    assert "%scale%" not in result.src_inner_xml
    assert "100%" in result.src_inner_xml
    assert "%paramFloor%" not in result.tgt_inner_xml
    assert "%scale%" not in result.tgt_inner_xml
    assert "100%" in result.tgt_inner_xml
    assert any(action["rule"] == "remove_percent_wrapped_tokens" for action in result.auto_actions)


def test_remove_percent_wrapped_tokens_does_not_remove_loose_percent_text():
    src = "Оставить это: % not a token % и 100%."
    tgt = "Keep this: % not a token % and 100%."
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="ru-RU",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_percent_wrapped_tokens=True,
            remove_game_markup=False,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == src
    assert result.tgt_inner_xml == tgt


def test_remove_percent_wrapped_tokens_can_be_disabled():
    src = "Damage from movement: %paramFloor%%, duration %scale% sec."
    tgt = "Урон: %paramFloor%%, длительность %scale% сек."
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="ru-RU",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_percent_wrapped_tokens=False,
            remove_game_markup=False,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert result.src_inner_xml == src
    assert result.tgt_inner_xml == tgt


def test_remove_game_markup_removes_encoded_color_tags():
    src = "Increases Health by &lt;Color=#51D052FF&gt;%param1%%&lt;/Color&gt;."
    tgt = (
        "Decreases damage by &lt;Color=#51D052FF&gt;%param1%%&lt;/Color&gt; "
        "and increases by &lt;Color=#51D052FF&gt;%param2%%&lt;/Color&gt;."
    )
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_percent_wrapped_tokens=False,
            remove_game_markup=True,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert "&lt;Color=" not in result.src_inner_xml
    assert "&lt;/Color&gt;" not in result.src_inner_xml
    assert "&lt;Color=" not in result.tgt_inner_xml
    assert "&lt;/Color&gt;" not in result.tgt_inner_xml
    assert "%param1%%" in result.src_inner_xml
    assert "%param1%%" in result.tgt_inner_xml
    assert "%param2%%" in result.tgt_inner_xml
    assert any(action["rule"] == "remove_game_markup" for action in result.auto_actions)


def test_remove_game_markup_removes_double_encoded_color_tags():
    src = "Increases Health by &amp;lt;Color=#51D052FF&amp;gt;%param1%%&amp;lt;/Color&amp;gt;."
    tgt = (
        "Decreases damage by &amp;lt;Color=#51D052FF&amp;gt;%param1%%&amp;lt;/Color&amp;gt; "
        "and increases by &amp;lt;Color=#51D052FF&amp;gt;%param2%%&amp;lt;/Color&amp;gt;."
    )
    result = analyze_and_clean_segments(
        src_inner_xml=src,
        tgt_inner_xml=tgt,
        src_lang="en-US",
        tgt_lang="en-US",
        options=CleanupOptions(
            normalize_spaces=False,
            remove_percent_wrapped_tokens=False,
            remove_game_markup=True,
            remove_inline_tags=False,
            remove_garbage_segments=False,
            emit_warnings=False,
        ),
    )

    assert "&amp;lt;Color=" not in result.src_inner_xml
    assert "&amp;lt;/Color&amp;gt;" not in result.src_inner_xml
    assert "&amp;lt;Color=" not in result.tgt_inner_xml
    assert "&amp;lt;/Color&amp;gt;" not in result.tgt_inner_xml
    assert "%param1%%" in result.src_inner_xml
    assert "%param1%%" in result.tgt_inner_xml
    assert "%param2%%" in result.tgt_inner_xml
    assert any(action["rule"] == "remove_game_markup" for action in result.auto_actions)
