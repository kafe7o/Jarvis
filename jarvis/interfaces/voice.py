"""Hands-free voice mode: say "Jarvis", then talk."""

from __future__ import annotations

import logging

from ..app import build, start_background
from ..config import settings
from ..confirm import is_yes

log = logging.getLogger("jarvis.voice")


def run() -> None:
    from ..voice.audio import Microphone
    from ..voice.speech import Speaker, Transcriber
    from ..voice.wake import WakeWord

    print("Зареждам гласовите модели…")
    transcribe = Transcriber(settings.whisper_model, settings.language)
    speak = Speaker(settings.tts_voice)
    mic = Microphone()
    mic.calibrate()

    def say(text: str) -> None:
        print(f"Jarvis: {text}")
        speak(text)
        mic.drain()

    def listen(wait: float = 6.0) -> str:
        audio = mic.record_utterance(wait_seconds=wait)
        text = transcribe(audio) if audio is not None else ""
        if text:
            print(f"Ти: {text}")
        return text

    def voice_confirm(summary: str) -> bool:
        say(f"Преди да продължа: {summary}. Потвърждаваш ли?")
        return is_yes(listen(8.0))

    jarvis, ctx = build(voice_confirm, [say])
    start_background(ctx)
    wake = WakeWord(mic, transcribe)
    say(f"На линия съм, {settings.user_name}.")

    while True:
        try:
            command = wake.wait()
            if not command:
                say("Да?")
                command = listen()
            # Keep the conversation going without the wake word while the user keeps talking.
            while command:
                try:
                    say(jarvis.ask(command))
                except Exception as exc:
                    log.exception("request failed")
                    say(f"Нещо се обърка: {exc}")
                command = listen(5.0)
        except KeyboardInterrupt:
            say("Изключвам се.")
            mic.close()
            break
