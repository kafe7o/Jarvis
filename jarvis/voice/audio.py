"""Microphone capture with a simple energy-based voice activity detector."""

from __future__ import annotations

import queue

import numpy as np

SAMPLE_RATE = 16_000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame.astype(np.float32))))) if frame.size else 0.0


class Microphone:
    """Streams 16 kHz mono int16 frames from the default input device."""

    def __init__(self, threshold: float = 500.0):
        import sounddevice as sd

        self.threshold = threshold
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
            callback=lambda data, *_: self._queue.put(data[:, 0].copy()),
        )
        self._stream.start()

    def frames(self):
        while True:
            yield self._queue.get()

    def drain(self) -> None:
        """Drop audio captured while Jarvis was talking."""
        while not self._queue.empty():
            self._queue.get_nowait()

    def calibrate(self, seconds: float = 1.0) -> None:
        """Set the speech threshold a bit above the room's background noise."""
        levels = []
        for frame in self.frames():
            levels.append(rms(frame))
            if len(levels) * FRAME >= seconds * SAMPLE_RATE:
                break
        self.threshold = max(300.0, float(np.percentile(levels, 90)) * 2.5)

    def record_utterance(self, wait_seconds: float = 6.0, max_seconds: float = 20.0, silence_seconds: float = 0.9) -> np.ndarray | None:
        """Wait for speech, then record until a pause. Returns int16 audio or None if nobody spoke."""
        frames: list[np.ndarray] = []
        started = False
        waited = silent = 0.0
        step = FRAME / SAMPLE_RATE
        pre_roll: list[np.ndarray] = []
        for frame in self.frames():
            loud = rms(frame) > self.threshold
            if not started:
                pre_roll = (pre_roll + [frame])[-4:]
                waited += step
                if loud:
                    started = True
                    frames.extend(pre_roll)
                elif waited >= wait_seconds:
                    return None
                continue
            frames.append(frame)
            silent = 0.0 if loud else silent + step
            if silent >= silence_seconds or len(frames) * step >= max_seconds:
                break
        return np.concatenate(frames)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
