r"""
Chunking — splitting a document into pieces the TTS model can handle.

Kokoro can only process so much text per request (~510 tokens; we've
verified ~1,100 characters works end to end through DeepInfra). A document
must therefore be split — but never mid-sentence, or the narration would
have bizarre breaks where the voice stops and restarts.

Strategy, gentlest cut first:
  1. Split the document into paragraphs (blank lines).
  2. Pack whole paragraphs into chunks up to the budget.
  3. A paragraph too big for one chunk gets split into sentences.
  4. A single sentence bigger than the whole budget (rare — legal texts,
     unpunctuated transcripts) gets hard-split at a space. Ugly but safe.

Run this file directly for a demo:
    .venv\Scripts\python.exe chunker.py
"""

import re

# Comfortably under the ~1,100 characters we verified DeepInfra's Kokoro
# handles in one request (fidelity test, 2026-07-29).
CHUNK_CHARS = 1000

# "End of sentence" = . ! ? or … (possibly followed by a closing quote),
# then whitespace. (?<=...) means "the character just before this spot".
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'”’]?\s+")


def split_into_chunks(text, budget=CHUNK_CHARS):
    """Split text into a list of chunks, each at most `budget` characters,
    breaking only at paragraph or sentence boundaries wherever possible."""
    # Step 1: collect budget-sized units (whole paragraphs, or sentence
    # groups cut from oversized paragraphs).
    units = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= budget:
            units.append(paragraph)
        else:
            units.extend(_split_long_paragraph(paragraph, budget))

    # Step 2: greedily pack units into chunks. Paragraphs joined inside one
    # chunk keep their blank line, so the voice still pauses between them.
    chunks = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= budget:
            current = candidate
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def locate_chapters(chunks, titles, keys):
    """Find where each chapter heading sits inside the chunks.

    `titles` are the display titles and `keys` the same headings after
    normalize(). Returns [{"title", "chunk", "offset"}] in reading order.

    Books often open with a table of contents that repeats every heading, so
    the first match is usually the wrong one. Headings are therefore matched
    as whole paragraphs, and placed from the last heading backwards: each one
    takes its latest occurrence that still comes before the next chapter.
    That lands on the real chapter starts instead of the contents page.
    """
    found = []
    for key in keys:
        positions = []
        if key:
            standalone = re.compile(r"(?:^|(?<=\n\n))" + re.escape(key) + r"(?=\n\n|$)")
            for index, chunk in enumerate(chunks):
                positions += [(index, m.start()) for m in standalone.finditer(chunk)]
            if not positions:
                # Fall back to an in-text match, but never "Chapter 1" inside "Chapter 12".
                loose = re.compile(re.escape(key) + r"(?!\w)")
                for index, chunk in enumerate(chunks):
                    positions += [(index, m.start()) for m in loose.finditer(chunk)]
        found.append(positions)

    placed = [None] * len(keys)
    limit = (len(chunks), 0)
    for i in range(len(keys) - 1, -1, -1):
        earlier = [p for p in found[i] if p < limit]
        if earlier:
            placed[i] = max(earlier)
            limit = placed[i]

    chapters = []
    for title, pos in zip(titles, placed):
        if pos is None:
            continue
        if chapters and (chapters[-1]["chunk"], chapters[-1]["offset"]) == pos:
            continue   # duplicate heading pointing at the same spot
        chapters.append({"title": title.strip()[:80], "chunk": pos[0], "offset": pos[1]})
    return chapters


def _split_long_paragraph(paragraph, budget):
    """Cut an oversized paragraph into budget-sized groups of sentences."""
    groups = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if len(sentence) > budget:
            # A single monster sentence: flush what we have, hard-split it.
            if current:
                groups.append(current)
                current = ""
            groups.extend(_hard_split(sentence, budget))
            continue
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) <= budget:
            current = candidate
        else:
            groups.append(current)
            current = sentence
    if current:
        groups.append(current)
    return groups


def _hard_split(sentence, budget):
    """Last resort: cut at the closest space under the budget."""
    parts = []
    while len(sentence) > budget:
        cut = sentence.rfind(" ", 0, budget)
        if cut <= 0:            # no space at all — cut mid-word, so be it
            cut = budget
        parts.append(sentence[:cut])
        sentence = sentence[cut:].strip()
    if sentence:
        parts.append(sentence)
    return parts


if __name__ == "__main__":
    demo = ("A short paragraph.\n\n" + "This sentence repeats to build a long "
            "paragraph. " * 40 + "\n\nAnother short one at the end.")
    result = split_into_chunks(demo)
    print(f"{len(demo)} chars -> {len(result)} chunks:")
    for i, chunk in enumerate(result, 1):
        print(f"  chunk {i}: {len(chunk)} chars, "
              f"starts {chunk[:40]!r}, ends {chunk[-40:]!r}")
