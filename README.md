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

- [x] EPUB extraction *(built 2026-07-29 — ebooklib + BeautifulSoup branch
      in `extract.py`; EPUBs extract cleaner than PDFs)*
- [x] Progressive playback — audio starts on the first finished chunk and
      hands over to the stitched file (with full seek bar) when the job
      completes *(built 2026-07-29)*
- [x] Voice picker — 10 curated Kokoro voices, validated server-side
- [x] Resume — the browser remembers your last listening position
      (localStorage; audio survives server restarts because the WAV is
      served from disk even after the in-memory job board forgets)
- [x] Skip ±15 s buttons — chunk-boundary-aware while a job is still
      generating (translates the target across per-chunk files)
- [x] Read-along panel — shows the narrated text; the current sentence is
      highlighted, finished sentences fade. Chunk position is exact
      (server-measured timings in `audio/{job}.json`); the sentence within
      a chunk is estimated by character-proportional timing, so it can
      drift ±1 sentence on unusual text.
- [x] Click-to-jump — click any sentence in the panel to seek there
- [x] Chapter navigation — EPUB headings become a jump-to-chapter dropdown
      (headings are matched inside the normalized chunks server-side)
- [x] Time-remaining estimate while generating
- [x] Sleep timer (15/30/60 min)
- [x] Media Session — lock-screen/media-key play/pause/skip where the
      browser allows it (some need HTTPS)
- [x] PWA manifest — "Add to Home Screen" gives an app-like launch

### Business-model decisions (2026-07-30 — context for later, not build tasks)

- **No "unlimited" tier.** Explicit generous caps instead — honest, bounds
  liability, and protects against the main abuse case: mass-producing
  audiobooks for redistribution.
- **Premium cap: monthly ceiling ~120 h of audio** (optionally + 24 h/day
  burst cap). Math at DeepInfra prices: 1 h audio ≈ 53k chars ≈ $0.033,
  so worst-case cost/user ≈ $4/month — *below* the planned $5–7 sub price,
  making it impossible to lose money on any user. 120 h/month is invisible
  to real listeners (~4–5 novels) but useless as a production pipeline.
- Enforcement needs accounts + persistent usage tracking (deliberately
  deferred). Jobs already record character counts and measured audio
  durations, so the meter mostly exists.

- [x] M4B audiobook output *(built 2026-07-30)* — after stitching, ffmpeg
      compresses the WAV into an .m4b (AAC 64k mono, faststart) with real
      chapter marks from the detected chapters; the audio route serves it.
      Requires ffmpeg (winget install Gyan.FFmpeg); without it, WAV remains.
- [x] Job cap raised to 1.5M characters (~28 h audio ≈ $0.93) — full-novel
      conversions in one job
- [x] Cleanup sweep — at each new job: finished books keep only .m4b+.json
      (WAV + chunks deleted), and only the 10 newest books are kept
- [x] Conversion library — browser-local list of past conversions, each
      resumable at its saved position; stale entries self-remove when the
      server no longer has the audio

### Fast-follows worth doing next (not yet built)
- Warm-up ping to hide the cold start; longer-term, self-host Kokoro
  (kills cold starts, the ~real-time synthesis ceiling, and per-char costs)

### Run the web app

```powershell
.venv\Scripts\python.exe -m uvicorn app:app --reload
```

### Listen from your phone (same Wi-Fi)

```powershell
.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0
```

- `--host 0.0.0.0` means "accept connections from other devices", not just
  this laptop. The first time, Windows Firewall will ask — allow Python on
  **private** networks.
- Find the laptop's address with `ipconfig` (the "IPv4 Address" of your
  Wi-Fi adapter, e.g. 192.168.1.23), then open `http://192.168.1.23:8000`
  on the phone.
- Phone-browser caveats: playback may need one tap on the player (mobile
  autoplay rules), and some browsers only enable lock-screen controls /
  "install as app" on HTTPS pages — those get properly solved by the
  eventual real deployment or Capacitor wrap, not worth fighting now.

Then open <http://127.0.0.1:8000>, paste text (≤3,000 chars for now), press
**Speak**. Key files: `app.py` (routes), `tts.py` (the only file that knows
about DeepInfra), `normalize.py` (notation → narrator words),
`static/index.html` (the page).
