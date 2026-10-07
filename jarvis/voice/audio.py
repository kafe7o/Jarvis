"""Запис от микрофона и възпроизвеждане на звук.

``SilenceDetector`` е чиста логика (без хардуер), за да може да се тества;
``Microphone`` и ``play_audio`` ползват ``sounddevice`` само когато са извикани.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import numpy as np

CHUNK_SECONDS = 0.03


def rms(chunk: np.ndarray) -> float:
    if chunk.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))


@dataclass
class SilenceDetector:
    """Решава кога човек е започнал и кога е спрял да говори.

    Прагът се изчислява от фоновия шум в първите ``calibration_chunks`` парчета.
    """

    chunk_seconds: float = CHUNK_SECONDS
    silence_seconds: float = 1.0
    max_seconds: float = 30.0
    start_timeout_seconds: float = 10.0
    calibration_chunks: int = 10
    min_threshold: float = 0.01
    noise_factor: float = 3.0

    _noise: list[float] = field(default_factory=list, init=False)
    _threshold: float | None = field(default=None, init=False)
    _started: bool = field(default=False, init=False)
    _silent_for: float = field(default=0.0, init=False)
    _elapsed: float = field(default=0.0, init=False)

    @property
    def started(self) -> bool:
        return self._started

    def feed(self, chunk: np.ndarray) -> bool:
        """Подава парче звук. Връща True, когато записът трябва да спре."""
        level = rms(chunk)
        self._elapsed += self.chunk_seconds

        if self._threshold is None:
            self._noise.append(level)
            if len(self._noise) >= self.calibration_chunks:
                self._threshold = max(
                    self.min_threshold, float(np.median(self._noise)) * self.noise_factor
                )
            return False

        if level >= self._threshold:
            self._started = True
            self._silent_for = 0.0
        elif self._started:
            self._silent_for += self.chunk_seconds

        if self._started and self._silent_for >= self.silence_seconds:
            return True
        if not self._started and self._elapsed >= self.start_timeout_seconds:
            return True
        return self._elapsed >= self.max_seconds


class Microphone:
    """Записва едно изказване: от първата дума до паузата след нея."""

    def __init__(self, sample_rate: int = 16000, silence_seconds: float = 1.0,
                 max_seconds: float = 30.0, start_timeout_seconds: float = 10.0):
        self.sample_rate = sample_rate
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.start_timeout_seconds = start_timeout_seconds

    def record_utterance(self) -> np.ndarray | None:
        """Връща моно float32 звук при ``sample_rate`` или None, ако никой не говори."""
        import sounddevice as sd

        detector = SilenceDetector(
            silence_seconds=self.silence_seconds,
            max_seconds=self.max_seconds,
            start_timeout_seconds=self.start_timeout_seconds,
        )
        blocksize = int(self.sample_rate * CHUNK_SECONDS)
        chunks: list[np.ndarray] = []
        with sd.InputStream(samplerate=self.sample_rate, channels=1,
                            dtype="float32", blocksize=blocksize) as stream:
            while True:
                data, _overflowed = stream.read(blocksize)
                chunk = data[:, 0].copy()
                chunks.append(chunk)
                if detector.feed(chunk):
                    break
        if not detector.started:
            return None
        return np.concatenate(chunks)


def play_audio(samples: np.ndarray, sample_rate: int) -> None:
    import sounddevice as sd

    sd.play(samples, sample_rate)
    sd.wait()


def play_mp3(data: bytes) -> None:
    """Пуска MP3 (от edge-tts). Първо през miniaudio, иначе през външен плейър."""
    try:
        import miniaudio
    except ImportError:
        miniaudio = None

    if miniaudio is not None:
        decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32,
                                   nchannels=1)
        samples = np.frombuffer(decoded.samples, dtype=np.float32)
        play_audio(samples, decoded.sample_rate)
        return

    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
                ["mpv", "--no-video", "--really-quiet"],
                ["afplay"]):
        if shutil.which(cmd[0]):
            with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
                f.write(data)
                f.flush()
                subprocess.run([*cmd, f.name], check=False)
            return
    raise RuntimeError("Няма как да пусна MP3: инсталирайте miniaudio (pip install miniaudio).")
