r"""
Text extraction — turning an uploaded file into clean, narrator-ready text.

Like tts.py, this is a boundary module: the ONE place that knows how to read
file formats. The rest of the app hands it a filename and raw bytes and gets
back a string (or a friendly ExtractionError). If pypdf ever disappoints us
on real-world PDFs, we swap in pdfplumber here and nothing else changes.

Why cleanup is half this file: PDFs don't store paragraphs — they store
lines positioned on a page. Extracted raw, you get hard line breaks
mid-sentence and words split by hyphenation ("exam-" / "ple"). Read that
aloud and every line break becomes a weird pause. So after extraction we
stitch lines back into paragraphs before anyone hears them.
"""

import html
import io
import os
import re
import tempfile

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub
from pypdf import PdfReader


class ExtractionError(Exception):
    """Raised when a file can't be turned into text, with a friendly reason."""


def extract_text(filename, data):
    """Take an uploaded file's name and raw bytes; return (clean text,
    list of chapter headings). Headings only exist for EPUBs — PDFs and
    plain text have no reliable way to mark them."""
    name = (filename or "").lower()

    if name.endswith(".txt"):
        return _tidy(_from_txt(data)), []
    if name.endswith(".pdf"):
        return _tidy(_from_pdf(data)), []
    if name.endswith(".epub"):
        text, headings = _from_epub(data)
        return _tidy(text), headings

    raise ExtractionError("Only .txt, .pdf, and .epub files are supported.")


def _from_txt(data):
    """Plain text just needs decoding from bytes to a string."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Older Windows files often use this encoding instead of UTF-8.
        return data.decode("cp1252", errors="replace")


def _from_pdf(data):
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        raise ExtractionError("That file doesn't appear to be a valid PDF.")

    if reader.is_encrypted:
        # Some PDFs are "encrypted" with an empty password — try that before
        # giving up.
        try:
            reader.decrypt("")
        except Exception:
            raise ExtractionError("This PDF is password-protected.")

    # One string per page; join with blank lines so page breaks read as
    # paragraph breaks. (extract_text() can return None for image-only pages,
    # hence the "or ''".)
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(pages)

    if not text.strip():
        raise ExtractionError(
            "No selectable text found in this PDF — it's probably a scan "
            "(pictures of pages). Reading scans needs OCR, which isn't built yet."
        )
    return text


def _from_epub(data):
    """An EPUB is a ZIP of web pages (XHTML chapters) plus a manifest saying
    what order they go in. Unlike PDFs, the paragraphs in it are REAL —
    no line-wrap or hyphenation damage to repair — so this is the cleanest
    input format the app has."""
    # ebooklib wants a file path, not bytes, so park the upload in a
    # temporary file for the duration of the parse.
    handle, temp_path = tempfile.mkstemp(suffix=".epub")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        try:
            book = epub.read_epub(temp_path)
        except Exception:
            raise ExtractionError("That file doesn't appear to be a valid EPUB.")
    finally:
        os.unlink(temp_path)

    paragraphs = []
    headings = []            # chapter/section titles, in reading order
    first_blocks = []        # first paragraph of each document (see below)
    # The "spine" is the book's reading order. Each entry names a chapter.
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue

        soup = BeautifulSoup(item.get_content(), "html.parser")

        # Pull text per block element (paragraphs, headings, list items…)
        # so real paragraph breaks survive as blank lines for _tidy.
        blocks = soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6",
                                "li", "blockquote"])
        if blocks:
            first_in_doc = None
            for block in blocks:
                # unescape: sloppy conversions leave double-escaped entities
                # ("&quot;") that would otherwise be narrated letter by letter.
                block_text = html.unescape(block.get_text(" ", strip=True))
                paragraphs.append(block_text)
                if block_text and first_in_doc is None:
                    first_in_doc = block_text
                # Top-level headings become chapter markers for navigation.
                if block.name in ("h1", "h2", "h3") and block_text:
                    headings.append(block_text)
            if first_in_doc:
                first_blocks.append(first_in_doc)
        else:
            # Some books mark paragraphs unusually (bare <div>s) — fall
            # back to grabbing all the chapter's text, one line per element.
            paragraphs.extend(
                html.unescape(line.strip())
                for line in soup.get_text("\n").splitlines()
            )

    # Calibre-converted books often have NO heading tags at all — each
    # chapter is its own document whose first paragraph is the title
    # ("Chapter 401 Divine Epiphany"). If the book yielded zero real
    # headings, accept those first paragraphs as chapters when they look
    # the part: short, and starting like a title. The pattern check stops
    # cover-page fragments from becoming phantom chapters.
    if not headings:
        title_like = re.compile(
            r"^(chapter|prologue|epilogue|interlude|part|book|act|volume|"
            r"appendix|afterword|foreword)\b|^\d+[\s.:—-]",
            re.IGNORECASE,
        )
        headings = [t for t in first_blocks
                    if len(t) <= 80 and title_like.match(t)]

    text = "\n\n".join(p for p in paragraphs if p)
    if not text.strip():
        raise ExtractionError("No readable text found in this EPUB.")
    return text, headings


def _tidy(text):
    """Stitch extracted lines back into paragraphs a narrator can read."""
    text = text.replace("\r\n", "\n")

    # Rejoin words hyphenated across a line break: "exam-\nple" -> "example".
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # A single newline is just a wrapped line — turn it into a space.
    # A blank line (two newlines) is a real paragraph break — keep it.
    # The (?<!\n) and (?!\n) parts mean "a \n NOT next to another \n".
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

    # Collapse runs of blank lines and repeated spaces left by all the above.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    return text.strip()
