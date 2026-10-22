"""Speech to text for the app's microphone when the browser cannot do it itself.

The app listens with the browser's own speech recognition, which works in Chrome and Edge. Other Chromium
browsers (Avast Secure Browser, Brave, Opera) have the same API but no access to Google's speech service, so it
fails with a "network" error. Then the app records the voice and sends it here: Whisper on this computer
(faster-whisper, installed with Jarvis), free and without internet. JARVIS_STT=whisper makes the app use it always.
"""

from __future__ import annotations

import os
import tempfile

MAX_BYTES = 24_000_000  # a few minutes of speech; a longer recording is said in parts
EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
       "audio/x-wav": "wav"}


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
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(data: bytes, mime: str = "audio/webm", language: str = "bg", model: str = "small") -> str:
    """The words in a short recording, heard by Whisper on this computer. Raises RuntimeError when it cannot."""
    if not data:
        return ""
    if len(data) > MAX_BYTES:
        raise ValueError("The recording is too long; say it in shorter parts.")
    try:
        return local(data, mime, language, model)
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed") from exc
    except Exception as exc:
        raise RuntimeError(f"local Whisper: {exc}") from exc
