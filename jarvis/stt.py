"""Speech to text for the app's microphone when the browser cannot do it itself.

The app listens with the browser's own speech recognition, which works in Chrome and Edge. Other Chromium
browsers (Avast Secure Browser, Brave, Opera) have the same API but no access to Google's speech service, so it
fails with a "network" error. Then the app records the voice and sends it here: Groq's Whisper (free with the
GROQ_API_KEY Jarvis already uses, very fast, good Bulgarian), else Whisper on this computer (faster-whisper),
else a plain error. JARVIS_STT=whisper makes the app use this way always.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.request
import uuid

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
MAX_BYTES = 24_000_000  # Groq's free tier takes files up to 25 MB
EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
       "audio/x-wav": "wav"}


def _multipart(fields: dict[str, str], filename: str, mime: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f"Content-Type: {mime}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def groq(data: bytes, mime: str, language: str) -> str:
    fields = {"model": os.environ.get("JARVIS_STT_MODEL") or GROQ_MODEL, "response_format": "json", "temperature": "0"}
    if language:
        fields["language"] = language
    body, content_type = _multipart(fields, f"voice.{EXT.get(mime.split(';')[0], 'webm')}", mime.split(";")[0], data)
    req = urllib.request.Request(GROQ_URL, data=body, headers={
        "Content-Type": content_type, "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
        "User-Agent": "jarvis"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return str(json.loads(resp.read().decode()).get("text") or "").strip()


def local(data: bytes, mime: str, language: str, model: str = "small") -> str:
    from .voice.speech import Transcriber

    with tempfile.NamedTemporaryFile(suffix="." + EXT.get(mime.split(";")[0], "webm"), delete=False) as f:
        f.write(data)
        path = f.name
    try:
        return Transcriber(model, language or None).file(path)
    finally:
        os.unlink(path)


def available() -> bool:
    if os.environ.get("GROQ_API_KEY"):
        return True
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(data: bytes, mime: str = "audio/webm", language: str = "bg", model: str = "small") -> str:
    """The words in a short recording; Groq first, then this computer. Raises RuntimeError when neither works."""
    if not data:
        return ""
    if len(data) > MAX_BYTES:
        raise ValueError("The recording is too long; say it in shorter parts.")
    errors = []
    if os.environ.get("GROQ_API_KEY"):
        try:
            return groq(data, mime, language)
        except Exception as exc:  # no internet, a busy minute: try this computer
            errors.append(f"Groq: {exc}")
    try:
        return local(data, mime, language, model)
    except ImportError:
        errors.append("faster-whisper is not installed")
    except Exception as exc:
        errors.append(f"local Whisper: {exc}")
    raise RuntimeError("; ".join(errors) or "no speech engine")
