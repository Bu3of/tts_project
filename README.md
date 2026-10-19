# Document-to-Speech

A small web app that turns PDF, EPUB or plain-text documents into narrated audio you can stream or download. Speech comes from [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), an open-weight TTS model, served through DeepInfra's API.

## Features

- Upload a PDF, EPUB or .txt file, or paste text. PDF line wraps and hyphenation are repaired; EPUB chapter headings are kept.
- Numbers, prices, times, ordinals and years are rewritten into words before synthesis, since Kokoro misreads things like "1,200" or "£3,000".
- Text is split into chunks of at most 1,000 characters, only at paragraph or sentence boundaries where possible.
- Conversion runs as a background job with a progress bar. Four chunks are synthesised in parallel and stitched back in order (a 6-chunk test job took 238 s, against about 420 s one at a time).
- Playback starts as soon as the first chunk is ready. The text panel highlights the sentence being read, and clicking a sentence jumps to it.
- Finished books export as M4B audiobooks with chapter marks (needs ffmpeg). The player remembers your position, has a sleep timer and chapter navigation, and can be installed as a PWA.

## How it works

`extract.py` (PDF/EPUB/text to clean text) → `normalize.py` (notation to spoken words) → `chunker.py` (sentence-aware chunks, chapter positions) → `tts.py` (Kokoro via DeepInfra, with retries) → `app.py` (FastAPI routes, background jobs, WAV stitching, M4B export) → `static/index.html` (player).

`tts.py` is the only file that talks to DeepInfra, so switching provider or self-hosting Kokoro means changing that one file.

## Running it locally

You need Python 3.10+, a [DeepInfra](https://deepinfra.com) API key, and optionally ffmpeg for M4B export.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste your key after DEEPINFRA_API_KEY=
uvicorn app:app --reload
```

Then open http://127.0.0.1:8000.

To listen on a phone on the same Wi-Fi, run `uvicorn app:app --host 0.0.0.0` and open `http://<your computer's IP>:8000`. There is no login, so only do this on a network you trust.

DeepInfra unloads the model when it's idle, so the first request after a break can take one to two minutes. Later requests take a second or two.

`hello_kokoro.py` is a minimal script that synthesises one sentence, useful for checking that your API key works.

## Tests

```bash
pip install pytest
pytest
```

The tests cover chunking, text normalization and chapter placement.

## Known limitations

- Jobs are tracked in memory, so restarting the server forgets jobs in progress. Finished audio stays on disk and can still be played.
- There are no accounts or authentication. It isn't meant to be deployed publicly as it is.
- Chapters come from EPUB heading tags, or chapter-like first lines in books that have none. PDFs and plain text get no chapters.
- The read-along highlight is exact per chunk but estimated within a chunk from character counts, so it can be off by about a sentence.
- Voices are English only. CJK characters are removed before synthesis.
- The 10 most recent conversions are kept; older ones are deleted when a new job starts.
