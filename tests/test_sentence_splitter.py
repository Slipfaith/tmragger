from core.splitter import propose_aligned_split, split_inner_xml_into_sentences


def test_split_inner_xml_into_sentences_splits_simple_text():
    parts = split_inner_xml_into_sentences("Hello world. Next sentence!")
    assert parts == ["Hello world.", "Next sentence!"]


def test_split_inner_xml_into_sentences_preserves_placeholder():
    parts = split_inner_xml_into_sentences('Hello <ph x="1" type="0"/>world. Next!')
    assert len(parts) == 2
    assert "<ph" in parts[0]
    assert "world." in parts[0]
    assert parts[1] == "Next!"


def test_split_inner_xml_into_sentences_handles_punctuation_followed_by_placeholder_and_uppercase():
    text = 'Let us go!<ph x="1" type="0" />Folio shines!<ph x="2" type="1" />Next line.'
    parts = split_inner_xml_into_sentences(text)
    assert len(parts) == 3
    assert parts[0] == "Let us go!"
    assert parts[1].startswith('<ph x="1"')
    assert "Folio shines!" in parts[1]
    assert parts[2].startswith('<ph x="2"')
    assert parts[2].endswith("Next line.")


def test_propose_aligned_split_returns_none_on_mismatch():
    src = "One. Two."
    tgt = "Один."
    assert propose_aligned_split(src, tgt) is None


def test_split_inner_xml_into_sentences_does_not_split_after_triple_dot():
    text = "The Lord knows that I... that we are doing our best!"
    parts = split_inner_xml_into_sentences(text)
    assert parts == [text]


def test_split_inner_xml_into_sentences_does_not_split_after_unicode_ellipsis():
    text = "Владыка знает, что я… мы стараемся как можем!"
    parts = split_inner_xml_into_sentences(text)
    assert parts == [text]


def test_split_inner_xml_into_sentences_does_not_split_after_known_abbreviations():
    cases = {
        "Das kostet z.B. fünf Euro. Es lohnt sich.": [
            "Das kostet z.B. fünf Euro.",
            "Es lohnt sich.",
        ],
        "Подходит напр. для теста. Второе предложение тут.": [
            "Подходит напр. для теста.",
            "Второе предложение тут.",
        ],
        "It costs approx. five euros. It is fine.": [
            "It costs approx. five euros.",
            "It is fine.",
        ],
    }
    for text, expected in cases.items():
        assert split_inner_xml_into_sentences(text) == expected


def test_split_inner_xml_into_sentences_splits_on_double_newline_paragraph_gap():
    text = (
        "Realm opening block without terminal punctuation\n\n"
        "Step one explains upgrades and workers\n\n"
        "Step two explains rewards and battles"
    )
    parts = split_inner_xml_into_sentences(text)
    assert parts == [
        "Realm opening block without terminal punctuation",
        "Step one explains upgrades and workers",
        "Step two explains rewards and battles",
    ]


def test_split_inner_xml_into_sentences_splits_on_qa_line_markers_without_punctuation():
    text = "Q: Realm basics\nA: Build your town\nQ: How to win\nA: Upgrade heroes"
    parts = split_inner_xml_into_sentences(text)
    assert parts == [
        "Q: Realm basics",
        "A: Build your town",
        "Q: How to win",
        "A: Upgrade heroes",
    ]


def test_propose_aligned_split_handles_multiline_faq_blocks():
    src = (
        "Q: Что такое царство\n"
        "A: Новый режим\n"
        "Q: Как усилиться\n"
        "A: Развивай Зал героев"
    )
    tgt = (
        "Q: What is the Realm\n"
        "A: A new mode\n"
        "Q: How to get stronger\n"
        "A: Upgrade the Hall of Heroes"
    )
    proposed = propose_aligned_split(src, tgt)
    assert proposed is not None
    src_parts, tgt_parts = proposed
    assert len(src_parts) == 4
    assert len(tgt_parts) == 4


def test_propose_aligned_split_reconciles_small_count_mismatch():
    src = "Intro line. Body line one. Body line two."
    tgt = "Вступление. Основной блок часть один. Основной блок часть два. Финал блока."
    proposed = propose_aligned_split(src, tgt)
    assert proposed is not None
    src_parts, tgt_parts = proposed
    assert len(src_parts) == 3
    assert len(tgt_parts) == 3


def test_propose_aligned_split_does_not_reconcile_when_one_side_has_single_part():
    src = "One sentence only"
    tgt = "Первая часть. Вторая часть."
    assert propose_aligned_split(src, tgt) is None


def test_propose_aligned_split_skips_two_short_sentences_by_default():
    src = "Hello world. Thanks all."
    tgt = "Привет мир. Спасибо всем."
    assert propose_aligned_split(src, tgt) is None


def test_propose_aligned_split_can_disable_short_sentence_guard():
    src = "Hello world. Thanks all."
    tgt = "Привет мир. Спасибо всем."
    proposed = propose_aligned_split(
        src,
        tgt,
        enable_short_sentence_pair_guard=False,
    )
    assert proposed is not None
    src_parts, tgt_parts = proposed
    assert len(src_parts) == 2
    assert len(tgt_parts) == 2


def test_propose_aligned_split_rejects_numeric_only_part():
    src = "1. Go to the Settings app on your device."
    tgt = "1. デバイスの[設定]アプリに移動します。"
    assert propose_aligned_split(src, tgt) is None


def test_propose_aligned_split_rejects_emoji_decoration_part():
    # Real pattern from production logs: the emoji chunk is identical on both
    # sides and cleanup would drop it — no need to isolate it into its own TU.
    src = "\u27a1\ufe0f \u2b05\ufe0f Take a closer look at the interior."
    tgt = "\u27a1\ufe0f \u2b05\ufe0f Хорошенько рассмотри обстановку."
    assert propose_aligned_split(src, tgt) is None


def test_propose_aligned_split_rejects_url_tail_part():
    # The URL chunk is identical across languages and would become a garbage TM
    # entry ("► URL" alone is not useful).
    src = "Subscribe to our channel! \u25ba http://bit.ly/YoutubeHWM"
    tgt = "Подпишитесь на наш канал! \u25ba http://bit.ly/YoutubeHWM"
    assert propose_aligned_split(src, tgt) is None


def test_propose_aligned_split_noise_guard_can_be_disabled():
    src = "Subscribe to our channel! \u25ba http://bit.ly/YoutubeHWM"
    tgt = "Подпишитесь на наш канал! \u25ba http://bit.ly/YoutubeHWM"
    assert (
        propose_aligned_split(src, tgt, enable_split_noise_guard=False) is not None
    )


def test_split_line_breaks_cuts_finished_lines_only():
    from core.splitter import split_inner_xml_into_sentences

    def split(text):
        return split_inner_xml_into_sentences(text, split_line_breaks=True)

    assert split("5 events – 7%\n6 events – 8%") == ["5 events – 7%", "6 events – 8%"]
    assert split("Cancelled:\n- all bonuses\n- free spins") == ["Cancelled:", "- all bonuses", "- free spins"]
    assert split("1. Open the app\n2. Enter the code") == ["1. Open the app", "2. Enter the code"]
    # Wrapped text and continuations stay together.
    assert len(split("Accumulator on several events \nwith odds multiplied.")) == 1
    assert len(split("Feel free to reach out\n— we are here to help")) == 1
    assert len(split("Best regards,\n1win")) == 1
    # Off by default.
    assert split_inner_xml_into_sentences("5 events – 7%\n6 events – 8%") == [
        "5 events – 7%\n6 events – 8%"
    ]


def test_split_line_breaks_falls_back_to_sentences_when_lines_do_not_align():
    from core.splitter import propose_aligned_split

    # Identical list lines would be rejected as noise; the sentence split must survive.
    src = "Players bet first. These are called:\n• Small Blind\n• Big Blind"
    tgt = "Spieler setzen zuerst. Diese heißen:\n• Small Blind\n• Big Blind"

    assert propose_aligned_split(src, tgt, split_line_breaks=True) == propose_aligned_split(src, tgt)


def test_glued_list_numbers_start_the_next_part():
    from core.splitter import split_inner_xml_into_sentences

    assert split_inner_xml_into_sentences(
        "1. Complete it on the platform2. Log in now! Win big!3. At the end we draw."
    ) == ["1. Complete it on the platform", "2. Log in now!", "Win big!", "3. At the end we draw."]
    # Not a list: wallet names and decimals keep the usual sentence split.
    assert split_inner_xml_into_sentences("Transfer to m10. The transfer is once") == [
        "Transfer to m10.",
        "The transfer is once",
    ]
    assert split_inner_xml_into_sentences("Settled at odds of 1.0. The bet is used.") == [
        "Settled at odds of 1.0.",
        "The bet is used.",
    ]


def test_list_number_after_sentence_stays_with_its_item():
    src = "Pick the odds. 2. Click Place bet. 3. Wait for the result."
    tgt = "Выберите коэффициент. 2. Нажмите Сделать ставку. 3. Дождитесь результата."

    parts = propose_aligned_split(src, tgt, enable_short_sentence_pair_guard=False)

    assert parts == (
        ["Pick the odds.", "2. Click Place bet.", "3. Wait for the result."],
        ["Выберите коэффициент.", "2. Нажмите Сделать ставку.", "3. Дождитесь результата."],
    )


def test_decimal_after_sentence_is_not_a_list_number():
    parts = split_inner_xml_into_sentences("Odds start at 1.5. Place a bet now.")
    assert parts == ["Odds start at 1.5.", "Place a bet now."]


def test_identical_noise_part_is_absorbed_instead_of_rejecting_the_split():
    src = "Place your bet. [table] Wait for the result."
    tgt = "Сделайте ставку. [table] Дождитесь результата."

    assert propose_aligned_split(src, tgt, enable_short_sentence_pair_guard=False) == (
        ["Place your bet.", "[table] Wait for the result."],
        ["Сделайте ставку.", "[table] Дождитесь результата."],
    )


def test_noise_part_on_its_own_line_keeps_its_line_break_when_absorbed():
    src = "Choose a seat.\n• Small Blind\nPlace your bet."
    tgt = "Выберите место.\n• Small Blind\nСделайте ставку."

    result = propose_aligned_split(src, tgt, enable_short_sentence_pair_guard=False, split_line_breaks=True)

    assert result == (
        ["Choose a seat.", "• Small Blind\nPlace your bet."],
        ["Выберите место.", "• Small Blind\nСделайте ставку."],
    )


def test_trailing_noise_part_joins_the_previous_part():
    src = "Place your bet. Wait for the result. [table]"
    tgt = "Сделайте ставку. Дождитесь результата. [table]"

    assert propose_aligned_split(src, tgt, enable_short_sentence_pair_guard=False) == (
        ["Place your bet.", "Wait for the result. [table]"],
        ["Сделайте ставку.", "Дождитесь результата. [table]"],
    )


def test_only_noise_and_one_real_part_is_not_split():
    assert propose_aligned_split("[table] Hello there.", "[table] Привет.", enable_short_sentence_pair_guard=False) is None
