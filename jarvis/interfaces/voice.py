"""Hands-free voice mode: say "Jarvis", then talk."""

from __future__ import annotations

import logging

from ..app import build, start_background
from ..config import settings, silence_seconds
from ..confirm import is_yes

log = logging.getLogger("jarvis.voice")


def run(shared=None) -> None:
    """shared: (jarvis, ctx) when several interfaces run in one process (jarvis serve)."""
    from ..voice.audio import Microphone
    from ..voice.speech import Speaker, Transcriber
    from ..voice.wake import WakeWord

    print("Зареждам гласовите модели…")
    transcribe = Transcriber(settings.whisper_model, settings.language)
    speak = Speaker(settings.tts_voice)
    mic = Microphone()
    mic.calibrate()
    lock = None
    if (settings.home / "voiceprint.npy").exists():
        from ..voice.speaker import VoiceLock

        lock = VoiceLock(settings.home / "voiceprint.npy")
        print("Гласова защита: слушам само собственика.")

    def owner(audio) -> bool:
        return lock is None or lock.is_owner(audio)

    def say(text: str) -> None:
        print(f"Jarvis: {text}")
        speak(text)
        mic.drain()

    def listen(wait: float = 6.0) -> tuple[str, object]:
        audio = mic.record_utterance(wait_seconds=wait, silence_seconds=silence_seconds())
        text = transcribe(audio) if audio is not None else ""
        if text:
            print(f"Ти: {text}")
        return text, audio

    def voice_confirm(summary: str) -> bool:
        say(f"Преди да продължа: {summary}. Потвърждаваш ли?")
        text, audio = listen(8.0)
        if text and not owner(audio):
            say("Това не беше твоят глас. Отказвам.")
            return False
        return is_yes(text)

    if shared:
        jarvis, ctx = shared
        ctx.notifiers.append(say)
    else:
        jarvis, ctx = build(voice_confirm, [say])
        start_background(ctx)
    wake = WakeWord(mic, transcribe)
    say(f"На линия съм, {settings.user_name}.")

    while True:
        try:
            command = wake.wait()
            audio = wake.last_audio
            if not command:
                say("Да?")
                command, audio = listen()
            # Keep the conversation going without the wake word while the user keeps talking.
            while command:
                if not owner(audio):
                    say("Не разпознавам гласа ти.")
                    break
                try:
                    say(jarvis.ask(command, confirmer=voice_confirm))
                except Exception as exc:
                    log.exception("request failed")
                    say(f"Нещо се обърка: {exc}")
                command, audio = listen(5.0)
        except KeyboardInterrupt:
            say("Изключвам се.")
            mic.close()
            break
