import numpy as np
import pytest

from jarvis.voice.brain import as_responder
from jarvis.voice.config import VoiceConfig
from jarvis.voice.session import VoiceSession, is_exit_phrase
from jarvis.voice.stt import Transcript


class FakeTTS:
    def __init__(self):
        self.said = []

    def speak(self, text, language=None):
        self.said.append((text, language))


class FakeSTT:
    def __init__(self, transcripts):
        self.transcripts = list(transcripts)

    def transcribe(self, audio, sample_rate=16000):
        return self.transcripts.pop(0)


class FakeMic:
    sample_rate = 16000

    def __init__(self, n):
        self.n = n

    def record_utterance(self):
        return np.zeros(160, dtype=np.float32)


def test_voice_loop_answers_until_goodbye():
    asked = []
    tts = FakeTTS()
    stt = FakeSTT([
        Transcript("Колко е часът?", "bg"),
        Transcript("  ", "bg"),
        Transcript("What's the weather?", "en"),
        Transcript("Довиждане!", "bg"),
    ])

    def respond(text):
        asked.append(text)
        return f"**Отговор** на: {text} Второ изречение."

    VoiceSession(respond, stt, tts, FakeMic(4), log=lambda _: None).run_voice()
    assert asked == ["Колко е часът?", "What's the weather?"]
    assert tts.said[0] == ("Отговор на: Колко е часът?", "bg")
    assert tts.said[1] == ("Второ изречение.", "bg")
    assert tts.said[2][1] == "en"
    assert tts.said[-1] == ("Довиждане, сър.", "bg")


def test_errors_are_spoken_not_raised():
    tts = FakeTTS()

    def respond(text):
        raise RuntimeError("няма мрежа")

    s = VoiceSession(respond, None, tts, log=lambda _: None)
    assert s.handle("здрасти") is True
    assert "грешка" in tts.said[0][0]


def test_keyboard_mode():
    tts = FakeTTS()
    lines = iter(["Здравей", "стоп"])
    VoiceSession(lambda t: "Здравейте, сър.", None, tts, log=lambda _: None).run_keyboard(
        lambda _: next(lines))
    assert [t for t, _ in tts.said] == ["Здравейте, сър.", "Довиждане, сър."]


@pytest.mark.parametrize("text,expected", [
    ("Довиждане", True), ("Стоп.", True), ("Jarvis, стоп", True), ("goodbye", True),
    ("Как да спра цигарите", False), ("Кажи ми какво е стоп кадър във филмите", False),
])
def test_exit_phrases(text, expected):
    assert is_exit_phrase(text) is expected


def test_voice_choice_follows_language():
    cfg = VoiceConfig()
    assert cfg.voice_for("bg") == "bg-BG-BorislavNeural"
    assert cfg.voice_for("en") == "en-GB-RyanNeural"
    assert cfg.voice_for("ja") == "en-GB-RyanNeural"
    cfg.voice = "bg-BG-KalinaNeural"
    assert cfg.voice_for("en") == "bg-BG-KalinaNeural"


def test_responder_adapts_core_objects():
    class Core:
        def ask(self, text):
            return text.upper()

    assert as_responder(Core)("a") == "A"
    assert as_responder(Core())("b") == "B"
    assert as_responder(lambda t: t + "!")("c") == "c!"
    with pytest.raises(TypeError):
        as_responder(42)


def test_tts_falls_back_once_primary_fails():
    from jarvis.voice.tts import FallbackTTS

    class Broken:
        calls = 0

        def speak(self, text, language=None):
            Broken.calls += 1
            raise OSError("няма интернет")

    backup = FakeTTS()
    tts = FallbackTTS(Broken(), lambda: backup, log=lambda _: None)
    tts.speak("едно", "bg")
    tts.speak("две", "bg")
    assert Broken.calls == 1
    assert backup.said == [("едно", "bg"), ("две", "bg")]
