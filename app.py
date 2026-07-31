r"""
The web server. As of Phase 4, converting a document works like a coat check:

    POST /api/jobs             hand over the text, get a claim ticket (job id);
                               synthesis starts in the background
    GET  /api/jobs/{id}        show the ticket: "chunk 7 of 23 done"
    GET  /api/jobs/{id}/audio  when done, collect the stitched WAV

The browser polls the status route every second or two and draws a progress
bar — the same ask-wait-ask-again pattern we first met with Replicate.

Run it with:
    .venv\Scripts\python.exe -m uvicorn app:app --reload

then open http://127.0.0.1:8000.
"""

import io
import json
import os
import re
import shutil
import subprocess
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chunker import split_into_chunks
from extract import ExtractionError, extract_text
from normalize import normalize
from tts import SynthesisError, synthesize

app = FastAPI(title="Document to Speech")

# Anything in the static/ folder is served under /static/... — the page,
# its icon, and later any CSS or JS files we split out.
app.mount("/static", StaticFiles(directory="static"), name="static")

# Finished audio lands here, one file per job. (*.wav is git-ignored.)
AUDIO_DIR = Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)

# The job board: job id -> progress info. Plain in-memory dict, which means
# a server restart forgets all jobs (and --reload restarts on every code
# edit!). Fine while the only user is us; a real deployment would keep this
# in a database — that's a later problem, deliberately.
JOBS = {}

# ~1.5M characters ≈ 28 hours of audio ≈ $0.93 of synthesis — room for a
# full novel in one job. A cap to catch accidents, not to ration.
MAX_JOB_CHARS = 1_500_000

# How many finished audiobooks to keep on disk (m4b + timing json).
# Older ones are deleted when a new job starts.
KEEP_JOBS = 10


# ---------------------------------------------------------------------------
# Pages and files
# ---------------------------------------------------------------------------

@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/favicon.ico")
def favicon():
    # Browsers ask for /favicon.ico by convention; point them at our SVG
    # so the request stops 404ing in the logs.
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Upload & extraction (Phase 3)
# ---------------------------------------------------------------------------

# A document big enough to be suspicious — a 500-page novel is ~1 MB of text.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    """Receive an uploaded file, send back its cleaned text as JSON."""
    data = await file.read()

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File is {len(data) // (1024 * 1024)} MB; the limit is 10 MB.",
        )

    try:
        text, headings = extract_text(file.filename, data)
    except ExtractionError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {
        "filename": file.filename,
        "characters": len(text),
        "text": text,
        "headings": headings[:200],   # chapter titles (EPUB only), for navigation
    }


# ---------------------------------------------------------------------------
# Conversion jobs (Phase 4)
# ---------------------------------------------------------------------------

# The voices the page offers. Validated here so arbitrary strings from the
# browser never reach the TTS API. (Kokoro has 54 voices; this is a curated
# English selection — af/am = American female/male, bf/bm = British.)
ALLOWED_VOICES = {
    "af_heart", "af_bella", "af_nicole", "af_sky", "am_adam",
    "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_fable",
}


class JobRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    headings: list[str] = []   # chapter titles from extraction, if any


def _sweep_audio_dir():
    """Housekeeping at each new job: finished audiobooks only need their
    .m4b + .json — the stitched WAV and per-chunk files are redundant once
    compression succeeded, and they're the bulky part (a WAV is ~15x its
    M4B). Then keep only the newest KEEP_JOBS audiobooks."""
    for m4b in AUDIO_DIR.glob("*.m4b"):
        (AUDIO_DIR / f"{m4b.stem}.wav").unlink(missing_ok=True)
        chunk_dir = AUDIO_DIR / m4b.stem
        if chunk_dir.is_dir():
            shutil.rmtree(chunk_dir, ignore_errors=True)

    finished = sorted(AUDIO_DIR.glob("*.m4b"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for old in finished[KEEP_JOBS:]:
        old.unlink(missing_ok=True)
        (AUDIO_DIR / f"{old.stem}.json").unlink(missing_ok=True)


@app.post("/api/jobs")
def create_job(request: JobRequest, background: BackgroundTasks):
    try:
        _sweep_audio_dir()
    except OSError:
        pass   # housekeeping must never block a conversion

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="There's no text to read.")
    if len(text) > MAX_JOB_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Text is {len(text):,} characters; the current limit is "
                   f"{MAX_JOB_CHARS:,}.",
        )
    if request.voice not in ALLOWED_VOICES:
        raise HTTPException(status_code=400, detail="Unknown voice.")

    # Normalize BEFORE chunking, so things like "£3,000" can't be sliced in
    # half by a chunk boundary, and the budget measures what will be spoken.
    chunks = split_into_chunks(normalize(text))
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail="After cleaning, no speakable text remained (an English "
                   "voice can't read this content).",
        )

    # Locate each chapter heading inside the chunks so the player can offer
    # jump-to-chapter. Headings are normalized the same way the text was —
    # otherwise "Chapter 3" would never match its spoken form "Chapter three".
    # A heading that isn't found (edited out, or an EPUB oddity) is dropped.
    chapters = []
    for title in request.headings[:200]:
        key = normalize(title).strip()[:60]
        if not key:
            continue
        for index, chunk in enumerate(chunks):
            offset = chunk.find(key)
            if offset != -1:
                if chapters and chapters[-1]["chunk"] == index \
                        and chapters[-1]["offset"] == offset:
                    break   # duplicate heading pointing at the same spot
                chapters.append({
                    "title": title.strip()[:80],
                    "chunk": index,
                    "offset": offset,   # character position inside the chunk
                })
                break

    job_id = uuid.uuid4().hex[:12]   # e.g. "b3f2a91c04d7" — random and unguessable
    JOBS[job_id] = {
        "status": "processing",      # -> "done" or "error"
        "done_chunks": 0,
        "total_chunks": len(chunks),
        "error": None,
    }

    # add_task means: send the response NOW, run _run_job right after.
    # The browser gets its claim ticket immediately and polls while we work.
    background.add_task(_run_job, job_id, chunks, request.voice, chapters)
    return {"job_id": job_id, "total_chunks": len(chunks)}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Unknown job. (Note: restarting the server forgets all jobs.)",
        )
    return job


@app.head("/api/jobs/{job_id}/audio")   # lets the page probe the format
@app.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str):
    # Route parameters come from the browser, i.e. from anyone. Since this
    # id becomes part of a file path, refuse anything that isn't plain
    # letters/digits — otherwise "../" tricks could reach files outside
    # the audio folder.
    if not job_id.isalnum():
        raise HTTPException(status_code=404, detail="Unknown job.")

    job = JOBS.get(job_id)
    if job is not None and job["status"] != "done":
        raise HTTPException(
            status_code=409,  # "conflict": the request is fine, the timing isn't
            detail=f"Job is {job['status']} ({job['done_chunks']}/"
                   f"{job['total_chunks']} chunks).",
        )

    # No entry in JOBS doesn't necessarily mean no audio: the files outlive
    # server restarts even though the in-memory job board doesn't. Serving
    # from disk here is what lets "resume listening" work across restarts.
    # Prefer the compressed audiobook; fall back to WAV (older conversions,
    # or ffmpeg unavailable).
    m4b = AUDIO_DIR / f"{job_id}.m4b"
    if m4b.exists():
        return FileResponse(m4b, media_type="audio/mp4")
    path = AUDIO_DIR / f"{job_id}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Unknown or expired conversion.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/jobs/{job_id}/meta")
def job_meta(job_id: str):
    """Read-along data: each chunk's text with its exact start time and
    duration, measured while stitching. Grows as the job progresses and,
    like the audio, survives server restarts (it's a file on disk)."""
    if not job_id.isalnum():
        raise HTTPException(status_code=404, detail="Unknown job.")
    path = AUDIO_DIR / f"{job_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No read-along data for this conversion.")
    return FileResponse(path, media_type="application/json")


@app.get("/api/jobs/{job_id}/chunks/{index}")
def job_chunk(job_id: str, index: int):
    """One chunk's audio, available as soon as THAT chunk is synthesized —
    this is what lets playback start while the rest still generates."""
    if not job_id.isalnum():
        raise HTTPException(status_code=404, detail="Unknown job.")
    path = AUDIO_DIR / job_id / f"{index:04d}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="That chunk isn't ready (or doesn't exist).")
    return FileResponse(path, media_type="audio/wav")


# DeepInfra serves Kokoro at roughly real-time speed per request (measured
# 2026-07-29: a ~60s chunk takes ~60s to synthesize). Sequentially, a book
# would take as long to convert as to listen to. Sending several requests at
# once fixes that: 4 in flight ≈ 4x the throughput.
MAX_PARALLEL_REQUESTS = 4


def _find_ffmpeg():
    """Locate ffmpeg: on PATH if the shell knew about it at server start,
    otherwise inside winget's install folder (a fresh install isn't visible
    to already-running programs until they restart)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    candidates = [winget / "Links" / "ffmpeg.exe",
                  *sorted((winget / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _ffmeta_escape(value):
    """ffmpeg's metadata file gives =, ;, # and \\ special meaning."""
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _transcode_to_m4b(job_id, wav_path, meta, chapters):
    """Compress the stitched WAV into an .m4b audiobook (AAC, ~15x smaller)
    with real chapter marks. Returns True on success; on any failure the
    WAV simply remains the playable artifact."""
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        return False

    total_ms = int((meta[-1]["start"] + meta[-1]["duration"]) * 1000)

    # ffmpeg reads chapters from a small "FFMETADATA" text file.
    lines = [";FFMETADATA1"]
    if meta:
        lines.append("title=" + _ffmeta_escape(meta[0]["text"][:60]))
    starts = []
    for chapter in chapters:
        chunk = meta[chapter["chunk"]]
        fraction = chapter["offset"] / max(len(chunk["text"]), 1)
        starts.append(int((chunk["start"] + fraction * chunk["duration"]) * 1000))
    for i, chapter in enumerate(chapters):
        end = starts[i + 1] if i + 1 < len(starts) else total_ms
        lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={starts[i]}", f"END={end}",
                  "title=" + _ffmeta_escape(chapter["title"])]

    ffmeta_path = AUDIO_DIR / f"{job_id}.ffmeta"
    ffmeta_path.write_text("\n".join(lines), encoding="utf-8")
    m4b_path = AUDIO_DIR / f"{job_id}.m4b"

    try:
        result = subprocess.run(
            [ffmpeg, "-y",
             "-i", str(wav_path),        # input 0: the audio
             "-i", str(ffmeta_path),     # input 1: title + chapter marks
             "-map_metadata", "1", "-map", "0:a",
             "-c:a", "aac", "-b:a", "64k", "-ac", "1",   # speech-grade AAC
             "-movflags", "+faststart",  # header up front -> browser can stream
             "-f", "ipod",               # the mp4 flavor .m4b players expect
             str(m4b_path)],
            capture_output=True, timeout=3600,
        )
        if result.returncode != 0:
            print("ffmpeg failed:", result.stderr.decode(errors="replace")[-500:])
            m4b_path.unlink(missing_ok=True)
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as error:
        print("ffmpeg error:", error)
        m4b_path.unlink(missing_ok=True)
        return False
    finally:
        ffmeta_path.unlink(missing_ok=True)


def _run_job(job_id, chunks, voice, chapters):
    """The background worker: synthesize chunks (several at a time), save
    each one as its own file for progressive playback, append it to the
    stitched WAV, and keep the job board updated for the poll route."""
    job = JOBS[job_id]
    chunk_dir = AUDIO_DIR / job_id       # per-chunk files live in here
    chunk_dir.mkdir(exist_ok=True)
    output_path = AUDIO_DIR / f"{job_id}.wav"
    meta_path = AUDIO_DIR / f"{job_id}.json"
    meta = []                # read-along data: text + timing per chunk
    start_seconds = 0.0
    output = None
    try:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as pool:
            # pool.map runs synthesize on up to 4 chunks simultaneously but
            # hands results back strictly IN ORDER — so stitching stays a
            # simple append, and the audio can never come out shuffled.
            # (Progress may pause then jump a few chunks at once; that's
            # just in-order delivery doing its job.)
            speak = partial(synthesize, voice=voice)
            for i, audio_bytes in enumerate(pool.map(speak, chunks)):
                # Each chunk saved alone -> playable the moment it's done...
                (chunk_dir / f"{i:04d}.wav").write_bytes(audio_bytes)

                # ...AND appended to the one big WAV. A WAV file is a small
                # header (sample rate etc.) + raw audio frames, so stitching
                # = write one header, then everyone's frames.
                with wave.open(io.BytesIO(audio_bytes)) as w:
                    if output is None:
                        output = wave.open(str(output_path), "wb")
                        output.setparams(w.getparams())
                    duration = w.getnframes() / w.getframerate()
                    output.writeframes(w.readframes(w.getnframes()))

                # Record exactly when this chunk starts and how long it lasts
                # — the read-along panel is driven by these numbers. Written
                # BEFORE done_chunks ticks up, so the data is always there
                # by the time the browser hears the chunk is ready.
                meta.append({
                    "text": chunks[i],
                    "start": round(start_seconds, 3),
                    "duration": round(duration, 3),
                })
                start_seconds += duration
                meta_path.write_text(
                    json.dumps({"chunks": meta, "chapters": chapters}),
                    encoding="utf-8",
                )

                job["done_chunks"] = i + 1

        # Close the stitched WAV (finalizes its header) before compressing.
        output.close()
        output = None

        # "finalizing" = all audio exists, ffmpeg is compressing it into an
        # .m4b audiobook. The page shows this; playback of chunks continues
        # undisturbed. If transcoding fails, the WAV stays and works.
        job["status"] = "finalizing"
        _transcode_to_m4b(job_id, output_path, meta, chapters)
        job["status"] = "done"
    except Exception as error:
        job["status"] = "error"
        job["error"] = str(error)
    finally:
        if output is not None:
            output.close()   # finalizes the header with the true total length
