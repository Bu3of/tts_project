# Document-to-Speech

Turns documents (PDF / text) into natural-sounding narration using
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M).

**Stack (settled 2026-07-29):** Python + FastAPI backend, plain HTML/JS
frontend, Kokoro-82M served by DeepInfra's hosted API, pypdf for PDF
extraction. Kokoro was chosen over pricier TTS models because per-character
cost is the business model, and it doesn't skip/hallucinate words on long
documents. Backups if DeepInfra ever fails: Replicate (account exists), or
run Kokoro locally via kokoro-onnx (free, CPU-only, no account needed).

## Current status: Phase 1 — smallest end-to-end loop

Hardcoded text → DeepInfra Kokoro API → playable WAV. Nothing more yet.

### One-time setup

1. **Get a DeepInfra API key** (the one step only you can do):
   - Sign up at <https://deepinfra.com> (GitHub or Google login works)
   - Add a payment method (~$0.80 per million characters — a test sentence
     costs a fraction of a cent). If Stripe's checkout page fails to load,
     it's an ad-blocker/privacy extension — use a clean browser.
   - Go to **Dashboard → API Keys → New API Key** and copy the key
2. **Store the key**: copy `.env.example` to a file named `.env`, and paste
   the key after `DEEPINFRA_API_KEY=`
3. Dependencies are already installed in `.venv/`. If you ever need to
   recreate it:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

### Run Phase 1

```powershell
.venv\Scripts\python.exe hello_kokoro.py
```

Then open `listen.html` in a browser and press play.

## Roadmap

> **Cold-start gotcha (measured 2026-07-29):** DeepInfra scales small TTS
> models to zero when idle. The first request after idle can take 1–2+
> minutes while the model boots; warm requests take ~1–2 s. Scripts use a
> 300 s timeout for this reason. Kokoro "hanging" almost certainly means a
> cold boot, not an outage.

> **Voice decision (locked by ear test, 2026-07-29):** Kokoro-82M with voice
> `af_heart`, fed through `normalize.py` (which rewrites "1,200", "£3,000",
> "8:15" etc. into narrator words — raw Kokoro misreads those). Chatterbox
> Turbo ($1.00/M, also on DeepInfra) read numbers correctly but had awkward
> pacing that no preprocessing of ours could fix; Kokoro's number problem
> was fixable in our own code, so Kokoro won.

- [x] Phase 0 — environment setup (Python 3.12, venv)
- [x] Phase 1 — hardcoded text → Kokoro → playable audio *(verified 2026-07-29)*
- [x] Phase 2 — FastAPI server + paste-text page *(built 2026-07-29)*
- [x] Phase 3 — file upload + PDF/text extraction (pypdf) *(built 2026-07-29)*
- [x] Phase 4 — sentence-aware chunking + background jobs + progress
      *(built 2026-07-29; synthesis runs 4 chunks in parallel because
      DeepInfra serves Kokoro at ~real-time speed per request — measured
      6-chunk job: 238 s parallel vs ~420 s sequential)*
- [x] Phase 5 — real player: speed control, download *(built 2026-07-29 —
      the full MVP is now working)*

### Fast-follows worth doing next (not yet built)

- MP3 downloads — WAV is ~3 MB per minute; an hour of audio is ~170 MB.
  Converting with ffmpeg would cut that ~6x.
- EPUB extraction (a third branch in `extract.py`)
- Job cleanup — old WAVs accumulate in `audio/`
- Warm-up ping to hide the cold start; longer-term, self-host Kokoro
  (kills cold starts, the ~real-time synthesis ceiling, and per-char costs)

### Run the web app (Phase 2)

```powershell
.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Then open <http://127.0.0.1:8000>, paste text (≤3,000 chars for now), press
**Speak**. Key files: `app.py` (routes), `tts.py` (the only file that knows
about DeepInfra), `normalize.py` (notation → narrator words),
`static/index.html` (the page).
