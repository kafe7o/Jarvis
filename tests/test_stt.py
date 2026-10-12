"""The app's microphone in a browser without its own speech service (Avast, Brave): Whisper through Groq."""

from __future__ import annotations

import base64
import json

import pytest

from jarvis import stt


class Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_groq_gets_the_recording_as_a_whisper_upload(monkeypatch):
    sent = []
    monkeypatch.setenv("GROQ_API_KEY", "gk")

    def urlopen(req, timeout=0):
        sent.append(req)
        return Resp(json.dumps({"text": " Пусни музика "}).encode())

    monkeypatch.setattr(stt.urllib.request, "urlopen", urlopen)
    assert stt.transcribe(b"OggS-voice", "audio/webm;codecs=opus") == "Пусни музика"
    req = sent[0]
    assert req.full_url == stt.GROQ_URL and req.get_header("Authorization") == "Bearer gk"
    body = req.data
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in body and b'name="language"\r\n\r\nbg' in body
    assert b'filename="voice.webm"' in body and b"OggS-voice" in body


def test_without_groq_this_computer_listens_and_without_both_it_says_what_to_do(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
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


def test_a_failing_groq_falls_back_to_this_computer(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")

    def down(req, timeout=0):
        raise OSError("no internet")

    monkeypatch.setattr(stt.urllib.request, "urlopen", down)
    monkeypatch.setattr(stt, "local", lambda data, mime, language, model="small": "локално")
    assert stt.transcribe(b"x") == "локално"


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
