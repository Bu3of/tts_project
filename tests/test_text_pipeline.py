"""Tests for the text side of the pipeline: normalization, chunking and
chapter placement. Run with:  pytest"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunker import CHUNK_CHARS, locate_chapters, split_into_chunks
from normalize import normalize


def test_chunks_respect_budget_and_keep_all_text():
    text = "Short intro.\n\n" + "A sentence that repeats. " * 200 + "\n\nThe end."
    chunks = split_into_chunks(text)
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_chunks_break_between_sentences():
    text = "One two three. " * 150
    for chunk in split_into_chunks(text.strip()):
        assert chunk.endswith(".")


def test_unbroken_text_is_hard_split():
    chunks = split_into_chunks("x" * 2500)
    assert [len(c) for c in chunks] == [1000, 1000, 500]


def test_normalize_money_time_and_years():
    out = normalize("On 3rd May 1943 the 8:15 train cost £3,000.")
    assert "third" in out
    assert "nineteen forty-three" in out
    assert "eight fifteen" in out
    assert "three thousand pounds" in out


def test_normalize_small_amounts():
    assert normalize("$0.50") == "fifty cents"
    assert normalize("$0.01") == "one cent"
    assert normalize("£1.99") == "one pound ninety-nine"


def test_chapters_skip_table_of_contents():
    body = "Filler sentence here. " * 60
    text = ("Contents\n\nChapter 1\n\nChapter 2\n\n" + body + "\n\nChapter 1\n\nBody one. "
            + "More text. " * 120 + "\n\nChapter 2\n\nBody two.")
    chunks = split_into_chunks(text)
    titles = ["Chapter 1", "Chapter 2"]
    chapters = locate_chapters(chunks, titles, titles)
    assert [c["title"] for c in chapters] == titles
    first, second = chapters
    assert first["chunk"] > 0                      # not the contents page
    assert (second["chunk"], second["offset"]) > (first["chunk"], first["offset"])


def test_chapter_1_does_not_match_chapter_12():
    chunks = ["Chapter 12 starts here and Chapter 1 is mentioned later in text."]
    chapters = locate_chapters(chunks, ["Chapter 1"], ["Chapter 1"])
    assert chapters[0]["offset"] == chunks[0].index("Chapter 1 is")
