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
