r"""
Phase 1 — the smallest possible end-to-end loop.

This script does exactly one thing: send a short, hardcoded piece of text to
Kokoro-82M running on DeepInfra, and save the audio it sends back as output.wav.

No file upload, no chunking, no web server yet. The point is to prove the core
loop (text in -> audio out) actually works before building anything on top of it.

Unlike Replicate's create-a-job-then-download approach, DeepInfra replies with
the audio bytes directly in a single request — one of the reasons we picked it.

Run it with:
    .venv\Scripts\python.exe hello_kokoro.py
"""

import os
import sys

import requests                  # the library that makes HTTP calls for us
from dotenv import load_dotenv   # reads the API key from the .env file

# ---------------------------------------------------------------------------
# Step 1: load the API key.
#
# load_dotenv() opens the .env file in this folder and makes every line in it
# available through os.getenv(). This keeps the secret key out of the code
# (and out of git — .env is listed in .gitignore).
# ---------------------------------------------------------------------------
load_dotenv()
API_KEY = os.getenv("DEEPINFRA_API_KEY")

if not API_KEY:
    sys.exit(
        "No API key found.\n"
        "1. Sign up at https://deepinfra.com and create an API key\n"
        "   (Dashboard -> API Keys -> New API Key)\n"
        "2. Copy .env.example to a file named .env\n"
        "3. Paste your key after DEEPINFRA_API_KEY=\n"
        "4. Run this script again"
    )

# ---------------------------------------------------------------------------
# Step 2: describe what we want synthesized.
#
# DeepInfra exposes Kokoro through an "OpenAI-compatible" endpoint — a common
# API shape many providers copy. That's deliberate future-proofing: switching
# to another provider (or another TTS model on DeepInfra itself) later is
# mostly a matter of changing the URL or the model name below.
# ---------------------------------------------------------------------------
URL = "https://api.deepinfra.com/v1/openai/audio/speech"

TEXT = (
    "Hello! This is Kokoro, reading a hardcoded test sentence. "
    "If you can hear this, the core loop of the app is working."
)

payload = {
    "model": "hexgrad/Kokoro-82M",
    "input": TEXT,
    "voice": "af_bella",       # one of Kokoro's 54 built-in voices; we can try others later
    "response_format": "wav",  # WAV is uncompressed and simple to work with for now
}

# The Authorization header is how the API knows the request is really from you.
headers = {"Authorization": f"Bearer {API_KEY}"}

# ---------------------------------------------------------------------------
# Step 3: make the request and check it worked.
#
# requests.post() sends our payload as JSON and waits for the reply.
# A status code of 200 means success; anything else, we print the server's
# error message so we can see exactly what went wrong and fix it.
#
# Why the generous 300s timeout: DeepInfra shuts these small TTS models down
# when nobody is using them, and the first request after idle has to wait for
# the model to boot — which we measured taking 1–2+ minutes. Once it's warm,
# requests finish in a second or two. (Discovered the hard way, 2026-07-29.)
# ---------------------------------------------------------------------------
print("Sending text to Kokoro via DeepInfra...")
print("(If the model is cold-booting, this can take a couple of minutes — be patient.)")
response = requests.post(URL, json=payload, headers=headers, timeout=300)

if response.status_code != 200:
    sys.exit(
        f"The API returned an error (status {response.status_code}):\n"
        f"{response.text}"
    )

# ---------------------------------------------------------------------------
# Step 4: save the audio.
#
# The response body is the raw bytes of a WAV file, so we just write them
# to disk. "wb" means write in binary mode (audio is bytes, not text).
# ---------------------------------------------------------------------------
with open("output.wav", "wb") as f:
    f.write(response.content)

size_kb = len(response.content) / 1024
print(f"Done! Saved output.wav ({size_kb:.0f} KB).")
print("Open listen.html in your browser to hear it.")
