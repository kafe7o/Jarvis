"""Гласовият разговор: слуша, разпознава, пита ядрото и отговаря на глас."""

from __future__ import annotations

import re
from typing import Callable, Protocol

import numpy as np

from jarvis.voice.brain import Responder
from jarvis.voice.stt import SpeechToText, Transcript
from jarvis.voice.text import clean_for_speech, split_sentences
from jarvis.voice.tts import TextToSpeech

EXIT_PHRASES = (
    "довиждане", "чао", "стоп", "спри", "изключи се", "край",
    "goodbye", "bye", "stop", "exit", "quit",
)
GOODBYE = {"bg": "Довиждане, сър.", "en": "Goodbye, sir."}

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


class Recorder(Protocol):
    sample_rate: int

    def record_utterance(self) -> np.ndarray | None: ...


def is_exit_phrase(text: str) -> bool:
    words = _PUNCT.sub(" ", text.lower()).split()
    normalized = " ".join(words)
    # Само кратки команди, за да не спре на "кажи ми как да спра цигарите".
    return len(words) <= 3 and any(
        normalized == p or normalized.startswith(p + " ") or normalized.endswith(" " + p)
        for p in EXIT_PHRASES
    )


class VoiceSession:
    def __init__(
        self,
        respond: Responder,
        stt: SpeechToText | None,
        tts: TextToSpeech | None,
        recorder: Recorder | None = None,
        default_language: str = "bg",
        log: Callable[[str], None] = print,
    ):
        self.respond = respond
        self.stt = stt
        self.tts = tts
        self.recorder = recorder
        self.default_language = default_language
        self.log = log

    def listen(self) -> Transcript | None:
        """Записва и разпознава едно изказване. None, ако не е казано нищо."""
        if self.recorder is None or self.stt is None:
            raise RuntimeError("Няма микрофон или разпознаване на реч")
        audio = self.recorder.record_utterance()
        if audio is None:
            return None
        transcript = self.stt.transcribe(audio, self.recorder.sample_rate)
        return transcript if transcript.text.strip() else None

    def speak(self, text: str, language: str | None = None) -> None:
        if self.tts is None:
            return
        spoken = clean_for_speech(text)
        for sentence in split_sentences(spoken):
            self.tts.speak(sentence, language or self.default_language)

    def handle(self, text: str, language: str | None = None) -> bool:
        """Обработва едно изказване. Връща False, когато разговорът трябва да спре."""
        language = language or self.default_language
        if is_exit_phrase(text):
            goodbye = GOODBYE.get(language, GOODBYE["en"])
            self.log(f"Jarvis: {goodbye}")
            self.speak(goodbye, language)
            return False
        try:
            answer = self.respond(text)
        except Exception as exc:  # грешка от API или мрежа не бива да спира гласа
            self.log(f"[Грешка: {exc}]")
            self.speak("Възникна грешка, опитайте отново." if language == "bg"
                       else "Something went wrong, please try again.", language)
            return True
        self.log(f"Jarvis: {answer}")
        self.speak(answer, language)
        return True

    def run_voice(self) -> None:
        """Разговор с микрофон: говорите, Jarvis отговаря, докато не кажете „довиждане“."""
        self.log("Слушам... (кажете „довиждане“ за край, Ctrl+C за изход)")
        while True:
            transcript = self.listen()
            if transcript is None:
                continue
            self.log(f"Вие: {transcript.text}")
            if not self.handle(transcript.text, transcript.language):
                return

    def run_keyboard(self, read: Callable[[str], str] = input) -> None:
        """Пишете на клавиатурата, Jarvis отговаря на глас. Удобно без микрофон."""
        while True:
            try:
                text = read("\nВие: ").strip()
            except EOFError:
                return
            if text and not self.handle(text):
                return
