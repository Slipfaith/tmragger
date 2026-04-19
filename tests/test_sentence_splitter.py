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
