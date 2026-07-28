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

import io
import re

from pypdf import PdfReader


class ExtractionError(Exception):
    """Raised when a file can't be turned into text, with a friendly reason."""


def extract_text(filename, data):
    """Take an uploaded file's name and raw bytes; return clean text."""
    name = (filename or "").lower()

    if name.endswith(".txt"):
        return _tidy(_from_txt(data))
    if name.endswith(".pdf"):
        return _tidy(_from_pdf(data))

    raise ExtractionError(
        "Only .txt and .pdf files are supported for now. (EPUB is on the roadmap.)"
    )


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
