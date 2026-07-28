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
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
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

# ~200k characters ≈ 4 hours of audio ≈ $0.13 of synthesis. Enough for a
# hefty book section; a cap mostly to catch accidents, not to ration.
MAX_JOB_CHARS = 200_000


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
        text = extract_text(file.filename, data)
    except ExtractionError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {"filename": file.filename, "characters": len(text), "text": text}


# ---------------------------------------------------------------------------
# Conversion jobs (Phase 4)
# ---------------------------------------------------------------------------

class JobRequest(BaseModel):
    text: str


@app.post("/api/jobs")
def create_job(request: JobRequest, background: BackgroundTasks):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="There's no text to read.")
    if len(text) > MAX_JOB_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Text is {len(text):,} characters; the current limit is "
                   f"{MAX_JOB_CHARS:,}.",
        )

    # Normalize BEFORE chunking, so things like "£3,000" can't be sliced in
    # half by a chunk boundary, and the budget measures what will be spoken.
    chunks = split_into_chunks(normalize(text))

    job_id = uuid.uuid4().hex[:12]   # e.g. "b3f2a91c04d7" — random and unguessable
    JOBS[job_id] = {
        "status": "processing",      # -> "done" or "error"
        "done_chunks": 0,
        "total_chunks": len(chunks),
        "error": None,
    }

    # add_task means: send the response NOW, run _run_job right after.
    # The browser gets its claim ticket immediately and polls while we work.
    background.add_task(_run_job, job_id, chunks)
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


@app.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    if job["status"] != "done":
        raise HTTPException(
            status_code=409,  # "conflict": the request is fine, the timing isn't
            detail=f"Job is {job['status']} ({job['done_chunks']}/"
                   f"{job['total_chunks']} chunks).",
        )
    return FileResponse(AUDIO_DIR / f"{job_id}.wav", media_type="audio/wav")


# DeepInfra serves Kokoro at roughly real-time speed per request (measured
# 2026-07-29: a ~60s chunk takes ~60s to synthesize). Sequentially, a book
# would take as long to convert as to listen to. Sending several requests at
# once fixes that: 4 in flight ≈ 4x the throughput.
MAX_PARALLEL_REQUESTS = 4


def _run_job(job_id, chunks):
    """The background worker: synthesize chunks (several at a time), append
    them to the output WAV in order, and keep the job board updated so the
    poll route has news to report."""
    job = JOBS[job_id]
    output_path = AUDIO_DIR / f"{job_id}.wav"
    output = None
    try:
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as pool:
            # pool.map runs synthesize on up to 4 chunks simultaneously but
            # hands results back strictly IN ORDER — so stitching stays a
            # simple append, and the audio can never come out shuffled.
            # (Progress may pause then jump a few chunks at once; that's
            # just in-order delivery doing its job.)
            for i, audio_bytes in enumerate(pool.map(synthesize, chunks)):
                # A WAV file is a small header (sample rate etc.) + raw audio
                # frames. Stitching = write one header, then everyone's frames.
                with wave.open(io.BytesIO(audio_bytes)) as w:
                    if output is None:
                        output = wave.open(str(output_path), "wb")
                        output.setparams(w.getparams())
                    output.writeframes(w.readframes(w.getnframes()))

                job["done_chunks"] = i + 1
        job["status"] = "done"
    except Exception as error:
        job["status"] = "error"
        job["error"] = str(error)
    finally:
        if output is not None:
            output.close()   # finalizes the header with the true total length
