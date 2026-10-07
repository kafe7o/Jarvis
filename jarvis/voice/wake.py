"""Wake word detection for "Jarvis".

Uses openWakeWord's pretrained "hey_jarvis" model when installed; otherwise
transcribes short utterances and looks for the word "Jarvis" / "Джарвис".
"""

from __future__ import annotations

import re

from .audio import Microphone

WAKE_PATTERN = re.compile(r"\b(hey\s+)?(jarvis|джарвис|жарвис|джарвиз|дарвис)\b[\s,.!?]*", re.I)


def strip_wake_word(text: str) -> str | None:
    """Return the command spoken after the wake word, '' if only the wake word, None if absent."""
    match = WAKE_PATTERN.search(text)
    if not match:
        return None
    return text[match.end():].strip(" ,.!?")


class WakeWord:
    def __init__(self, mic: Microphone, transcribe, threshold: float = 0.5):
        self.mic = mic
        self.transcribe = transcribe
        self.threshold = threshold
        try:
            import openwakeword
            from openwakeword.model import Model

            openwakeword.utils.download_models(["hey_jarvis"])
            self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception:
            self.model = None

    def wait(self) -> str:
        """Block until the wake word is heard. Returns any command spoken in the same breath."""
        if self.model is not None:
            self.model.reset()
            for frame in self.mic.frames():
                scores = self.model.predict(frame)
                if max(scores.values()) >= self.threshold:
                    return ""
        while True:
            audio = self.mic.record_utterance(wait_seconds=3600, max_seconds=10)
            if audio is None or len(audio) < 4000:
                continue
            command = strip_wake_word(self.transcribe(audio))
            if command is not None:
                return command

