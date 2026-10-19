r"""
The synthesis module — the ONE place in the app that knows about DeepInfra.

Every design discussion we've had ends the same way: "and if that ever
changes, it's one small module to swap." This is that module. If we ever
move to another provider, another model, or a self-hosted Kokoro, only this
file changes; the rest of the app just calls synthesize(text) and gets
audio bytes back, without knowing or caring where they came from.
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEEPINFRA_API_KEY")
URL = "https://api.deepinfra.com/v1/openai/audio/speech"

MODEL = "hexgrad/Kokoro-82M"
DEFAULT_VOICE = "af_heart"   # chosen by ear test, 2026-07-29

# DeepInfra unloads small models when idle; the first request after that has
# to wait for the model to boot (measured: 1-2+ minutes). Warm requests take
# a second or two. Hence the patient timeout.
TIMEOUT_SECONDS = 300

# Attempts per chunk for transient failures (network errors, 429, 5xx).
MAX_ATTEMPTS = 3


class SynthesisError(Exception):
    """Raised when synthesis fails, with a human-readable explanation."""


def synthesize(text, voice=DEFAULT_VOICE):
    """Turn text into WAV audio bytes. Raises SynthesisError on failure.

    Note: callers should normalize() the text first — this function
    deliberately doesn't do it, so the two concerns stay separately testable.
    """
    if not API_KEY:
        raise SynthesisError(
            "No DEEPINFRA_API_KEY found. Copy .env.example to .env and add your key."
        )

    # A long book is hundreds of requests, so one dropped connection or a
    # brief 5xx shouldn't fail the whole job. Retry those a few times with a
    # growing pause; anything else (bad key, bad input) fails immediately.
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            response = requests.post(
                URL,
                json={
                    "model": MODEL,
                    "input": text,
                    "voice": voice,
                    "response_format": "wav",
                },
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            last_error = SynthesisError(f"Request to DeepInfra failed: {error}")
            continue

        if response.status_code == 200:
            return response.content
        last_error = SynthesisError(
            f"DeepInfra returned status {response.status_code}: {response.text[:300]}"
        )
        if response.status_code != 429 and response.status_code < 500:
            break

    raise last_error
