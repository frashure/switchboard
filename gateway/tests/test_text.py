from switchboard.text import split_for_speech


def test_empty_and_whitespace():
    assert split_for_speech("") == []
    assert split_for_speech("  \n\n ") == []


def test_short_text_is_one_chunk():
    assert split_for_speech("Sure.") == ["Sure."]
    assert split_for_speech("Hello there. This is a test.") == ["Hello there. This is a test."]


def test_abbreviations_initials_and_decimals_do_not_split():
    text = "Dr. Smith met J. R. R. Tolkien at 3.5 p.m. in St. Louis. They talked for hours."
    joined = " ".join(split_for_speech(text))
    assert joined == text
    for chunk in split_for_speech(text):
        assert not chunk.endswith(("Dr.", "St.", "R."))


def test_paragraphs_split():
    text = "First paragraph is here and long enough to stand.\n\nSecond paragraph follows right after the first one."
    assert len(split_for_speech(text)) == 2


def test_short_tail_is_merged_into_previous_chunk():
    text = "First paragraph is here and long enough to stand.\n\nOkay then."
    assert len(split_for_speech(text)) == 1


def test_first_chunk_is_short_even_for_a_long_sentence():
    sentence = "word " * 80  # one 400-char run-on
    chunks = split_for_speech(sentence.strip() + ".")
    assert len(chunks[0]) <= 120
    assert all(len(c) <= 350 for c in chunks)


def test_no_text_lost_or_duplicated():
    text = " ".join(f"Sentence number {i} says something worth hearing." for i in range(30))
    assert " ".join(split_for_speech(text)) == text
