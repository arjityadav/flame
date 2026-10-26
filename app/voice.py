"""Voice through ElevenLabs, proxied so the key never reaches the browser.

Speech to text: Scribe (POST /v1/speech-to-text, model scribe_v2), with key terms from the run so regulation words
("retro-reflector", "S3", "Annex 8") are recognised. Text to speech: POST /v1/text-to-speech/{voice}/stream.

Configuration (.env): ELEVENLABS_KEY (or ELEVENLABS_API_KEY), optional ELEVENLABS_VOICE_ID and ELEVENLABS_TTS_MODEL.
"""

from __future__ import annotations

import os

import httpx

API = "https://api.elevenlabs.io/v1"
STT_MODEL = "scribe_v2"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # a stock ElevenLabs voice; override with ELEVENLABS_VOICE_ID


def _key() -> str:
    return (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_KEY") or "").strip().strip('"')


def enabled() -> bool:
    return bool(_key())


def _headers() -> dict:
    return {"xi-api-key": _key()}


def transcribe(audio: bytes, filename: str, content_type: str, keyterms: list[str]) -> str:
    data = [("model_id", STT_MODEL), ("language_code", "en")]
    data += [("keyterms", t) for t in keyterms[:50] if t]
    with httpx.Client(timeout=15) as client:
        resp = client.post(f"{API}/speech-to-text", headers=_headers(), data=data,
                           files={"file": (filename, audio, content_type)})
        if resp.status_code == 422 and keyterms:  # key terms rejected: retry plain rather than fail the question
            resp = client.post(f"{API}/speech-to-text", headers=_headers(), data=data[:2],
                               files={"file": (filename, audio, content_type)})
        resp.raise_for_status()
        return resp.json().get("text", "").strip()


def speak(text: str):
    """Yield MP3 bytes as ElevenLabs produces them."""
    voice = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)
    body = {"text": text[:1500]}
    if model := os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"):
        body["model_id"] = model
    url = f"{API}/text-to-speech/{voice}/stream?output_format=mp3_44100_128"
    with httpx.Client(timeout=httpx.Timeout(10, read=30)) as client:
        with client.stream("POST", url, headers=_headers(), json=body) as resp:
            if resp.status_code in (400, 422) and "model_id" in body:  # model not available to this account
                resp.close()
                body.pop("model_id")
                with client.stream("POST", url, headers=_headers(), json=body) as retry:
                    retry.raise_for_status()
                    yield from retry.iter_bytes()
                return
            resp.raise_for_status()
            yield from resp.iter_bytes()
