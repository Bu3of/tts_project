r"""
Text normalization for narration.

TTS models pronounce *words* well but stumble on *notation* — things humans
read with their eyes and silently translate: "1,200", "£3,000", "8:15", "3rd".
Our listening test (2026-07-29) showed Kokoro reading "1,200" as "one...two
hundred" and "£3,000" as nonsense, while handling actual prose beautifully.

The fix is to translate notation into words BEFORE the text reaches the
model. Each rule below is a regular expression (a pattern that finds text
matching a shape, like "digits with commas") paired with a small function
that rewrites what it found.

Order matters: currency must run before plain numbers (so the £ isn't
orphaned), and comma-grouped numbers before years (so "1,943" isn't
mistaken for a year).

This module will grow as we listen to more real documents — that's expected.
Run this file directly to see a before/after demo:
    .venv\Scripts\python.exe normalize.py
"""

import re

from num2words import num2words


def _words(number_text):
    """'1,200' -> 'one thousand two hundred' (commas stripped from output,
    because num2words writes 'one thousand, two hundred' and that comma
    would make the TTS pause mid-number — the exact bug we're fixing)."""
    n = float(number_text.replace(",", ""))
    n = int(n) if n == int(n) else n
    return num2words(n).replace(",", "")


# --- individual rules -------------------------------------------------------

CURRENCY_NAMES = {"£": ("pound", "pence"), "$": ("dollar", "cents"), "€": ("euro", "cents")}


def _currency(match):
    """'£3,000' -> 'three thousand pounds'; '£3.50' -> 'three pounds fifty'."""
    symbol, amount = match.group(1), match.group(2)
    unit, subunit = CURRENCY_NAMES[symbol]
    value = float(amount.replace(",", ""))
    whole = int(value)
    cents = round((value - whole) * 100)
    text = f"{_words(str(whole))} {unit}{'' if whole == 1 else 's'}"
    if cents:
        text += f" {_words(str(cents))}"
    return text


def _time(match):
    """'8:15' -> 'eight fifteen'; '8:00' -> 'eight o'clock'; '8:05' -> 'eight oh five'."""
    hour, minute = match.group(1), match.group(2)
    if minute == "00":
        return f"{_words(hour)} o'clock"
    if minute.startswith("0"):
        return f"{_words(hour)} oh {_words(minute[1])}"
    return f"{_words(hour)} {_words(minute)}"


def _ordinal(match):
    """'3rd' -> 'third'."""
    return num2words(int(match.group(1)), to="ordinal").replace(",", "")


def _year(match):
    """'1943' -> 'nineteen forty-three' (num2words would otherwise say
    'one thousand nine hundred and forty-three', which no narrator does)."""
    return num2words(int(match.group(0)), to="year").replace(",", "")


def _comma_number(match):
    """'1,200' -> 'one thousand two hundred'."""
    return _words(match.group(0))


# --- the pipeline -----------------------------------------------------------

# (pattern, rewrite function) pairs, applied top to bottom.
RULES = [
    (re.compile(r"([£$€])\s?(\d[\d,]*(?:\.\d+)?)"), _currency),
    (re.compile(r"\b(\d{1,2}):(\d{2})\b"), _time),
    (re.compile(r"\b(\d+)(?:st|nd|rd|th)\b"), _ordinal),
    (re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"), _comma_number),
    (re.compile(r"\b(1[5-9]\d\d|20\d\d)\b"), _year),
]


def normalize(text):
    """Rewrite notation into narrator-friendly words."""
    for pattern, rewrite in RULES:
        text = pattern.sub(rewrite, text)
    # Collapse any doubled-up spaces the rewrites may have left behind.
    return re.sub(r" {2,}", " ", text)


# Running the file directly shows what the rules do — handy while developing.
if __name__ == "__main__":
    demo = (
        "On March 3rd, 1943, the 8:15 train was late. The alloy tolerated "
        "1,200 degrees, and the board allocated £3,000 for it. Coffee was $4.50."
    )
    print("BEFORE:", demo)
    print("AFTER: ", normalize(demo))
