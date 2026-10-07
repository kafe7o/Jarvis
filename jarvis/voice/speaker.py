"""Voice lock: Jarvis only obeys the owner's voice.

`jarvis enroll-voice` records a few sentences and stores a voiceprint in ~/.jarvis.
From then on, voice mode ignores commands and confirmations from other voices.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE

ENROLL_PHRASES = [
    "Джарвис, добро утро. Какво имам днес?",
    "Пусни музика и намали светлините в хола.",
    "Напомни ми утре в девет да се обадя на мама.",
    "Колко е часът и какво е времето навън?",
    "Това е моят глас и само мен слушай.",
]


class VoiceLock:
    def __init__(self, path: Path, threshold: float = 0.72):
        from resemblyzer import VoiceEncoder

        self.path = path
        self.threshold = threshold
        self.encoder = VoiceEncoder()
        self.print = np.load(path) if path.exists() else None

    @property
    def enabled(self) -> bool:
        return self.print is not None

    def embed(self, audio: np.ndarray) -> np.ndarray:
        from resemblyzer import preprocess_wav

        wav = preprocess_wav(audio.astype(np.float32) / 32768.0, source_sr=SAMPLE_RATE)
        return self.encoder.embed_utterance(wav)

    def enroll(self, samples: list[np.ndarray]) -> None:
        embeds = np.stack([self.embed(s) for s in samples])
        mean = embeds.mean(axis=0)
        self.print = mean / np.linalg.norm(mean)
        np.save(self.path, self.print)

    def similarity(self, audio: np.ndarray) -> float:
        emb = self.embed(audio)
        return float(np.dot(emb, self.print) / np.linalg.norm(emb))

    def is_owner(self, audio: np.ndarray | None) -> bool:
        if not self.enabled:
            return True
        return audio is not None and len(audio) > SAMPLE_RATE // 2 and self.similarity(audio) >= self.threshold


def enroll(settings) -> None:
    try:
        import resemblyzer  # noqa: F401
    except ImportError:
        raise SystemExit('Гласовата защита иска допълнителен пакет: pip install -e ".[voicelock]"\n'
                         "(на Windows първо: Visual Studio Build Tools с „Desktop development with C++“)")
    from .audio import Microphone

    mic = Microphone()
    mic.calibrate()
    lock = VoiceLock(settings.home / "voiceprint.npy")
    samples = []
    print("Прочети на глас всяко изречение след като се появи:")
    for phrase in ENROLL_PHRASES:
        print(f"\n  „{phrase}“")
        audio = mic.record_utterance(wait_seconds=10)
        while audio is None:
            print("  Не те чух, пробвай пак.")
            audio = mic.record_utterance(wait_seconds=10)
        samples.append(audio)
    lock.enroll(samples)
    mic.close()
    print("\nГотово. Jarvis вече слуша само твоя глас (изтрий ~/.jarvis/voiceprint.npy, за да го изключиш).")
