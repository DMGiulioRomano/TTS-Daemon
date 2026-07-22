"""The sentence splitter: terminators, abbreviation/initial guards, hard-split."""

from __future__ import annotations

from tts_daemon.core.chunking import split_into_chunks


class TestSentenceSplitting:
    def test_splits_on_each_terminator(self) -> None:
        assert split_into_chunks("Hello world. How are you? I am fine!") == [
            "Hello world.",
            "How are you?",
            "I am fine!",
        ]

    def test_ellipsis_and_runs_are_one_boundary(self) -> None:
        assert split_into_chunks("Wait... What?! Go.") == ["Wait...", "What?!", "Go."]

    def test_no_terminator_is_a_single_chunk(self) -> None:
        assert split_into_chunks("just one clause with no end") == ["just one clause with no end"]

    def test_whitespace_is_stripped_and_blanks_dropped(self) -> None:
        assert split_into_chunks("  One.   Two.  \n ") == ["One.", "Two."]

    def test_terminator_needs_trailing_space(self) -> None:
        # 3.14 and file.txt have no space after the dot, so they never split.
        assert split_into_chunks("Pi is 3.14 exactly. Open file.txt now.") == [
            "Pi is 3.14 exactly.",
            "Open file.txt now.",
        ]

    def test_blank_text_yields_no_chunks(self) -> None:
        assert split_into_chunks("   \n\t ") == []
        assert split_into_chunks("") == []


class TestAbbreviationGuards:
    def test_titles_do_not_split(self) -> None:
        assert split_into_chunks("Dr. Smith met Mr. Lee today. They talked.") == [
            "Dr. Smith met Mr. Lee today.",
            "They talked.",
        ]

    def test_ambiguous_abbreviation_is_allowed_to_split(self) -> None:
        # `etc.` is as often sentence-final as not, so — unlike a title — it is
        # deliberately NOT guarded: splitting here is the common-case correct
        # behaviour, and never glues two sentences into one clip.
        assert split_into_chunks("Bring pens, paper, etc. Then start.") == [
            "Bring pens, paper, etc.",
            "Then start.",
        ]

    def test_single_letter_initials_do_not_split(self) -> None:
        assert split_into_chunks("Use e.g. a pen. Or i.e. a pencil.") == [
            "Use e.g. a pen.",
            "Or i.e. a pencil.",
        ]
        assert split_into_chunks("J. R. R. Tolkien wrote it. Truly.") == [
            "J. R. R. Tolkien wrote it.",
            "Truly.",
        ]

    def test_internal_dotted_acronym_stays_whole(self) -> None:
        assert split_into_chunks("The U.S.A. is large. Indeed.") == [
            "The U.S.A. is large.",
            "Indeed.",
        ]


class TestHardSplit:
    def test_long_sentence_is_split_at_whitespace(self) -> None:
        text = "word " * 50  # 250 chars, no terminator
        chunks = split_into_chunks(text, max_chars=40)
        assert len(chunks) > 1
        assert all(len(chunk) <= 40 for chunk in chunks)
        # No text is lost and no leading/trailing spaces survive.
        assert "".join(chunk.replace(" ", "") for chunk in chunks) == "word" * 50

    def test_unbroken_token_is_cut_at_the_limit(self) -> None:
        chunks = split_into_chunks("x" * 25, max_chars=10)
        assert chunks == ["x" * 10, "x" * 10, "x" * 5]

    def test_short_sentences_are_not_hard_split(self) -> None:
        assert split_into_chunks("One. Two. Three.", max_chars=40) == [
            "One.",
            "Two.",
            "Three.",
        ]
