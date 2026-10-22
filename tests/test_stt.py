"""The app's microphone in a browser without its own speech service (Avast, Brave): Whisper on this computer."""

from __future__ import annotations

import base64
import json

import pytest

from jarvis import stt


def test_this_computer_listens_and_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(stt, "local", lambda data, mime, language, model="small": "здрасти")
    assert stt.transcribe(b"x") == "здрасти"

    def missing(*a, **k):
        raise ImportError("faster_whisper")

    monkeypatch.setattr(stt, "local", missing)
    with pytest.raises(RuntimeError, match="faster-whisper is not installed"):
        stt.transcribe(b"x")
    with pytest.raises(ValueError):
        stt.transcribe(b"x" * (stt.MAX_BYTES + 1))
    assert stt.transcribe(b"") == ""


def test_the_app_posts_its_recording_and_gets_the_words(settings, ctx, registry, monkeypatch):
    from test_hub import call, make_hub

    hub, port = make_hub(settings, ctx, registry, [])
    got = []
    monkeypatch.setattr(stt, "transcribe", lambda data, mime, lang, model: got.append((data, mime, lang)) or "колко е часът")
    audio = "data:audio/webm;codecs=opus;base64," + base64.b64encode(b"voice").decode()
    status, body = call(port, "/api/stt", {"audio": audio, "lang": "bg"})
    assert status == 200 and json.loads(body) == {"text": "колко е часът"}
    assert got == [(b"voice", "audio/webm", "bg")]
    me = json.loads(call(port, "/api/me")[1])
    assert me["stt"] == ""
