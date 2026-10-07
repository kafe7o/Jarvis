import numpy as np

from jarvis.voice.audio import SilenceDetector
from jarvis.voice.stt import to_wav_bytes, _iso_code


def _chunks(level, n):
    return [np.full(480, level, dtype=np.float32) for _ in range(n)]


def test_stops_after_speech_and_silence():
    d = SilenceDetector(chunk_seconds=0.03, silence_seconds=0.3, calibration_chunks=5)
    stream = _chunks(0.001, 5) + _chunks(0.3, 20) + _chunks(0.001, 20)
    stopped_at = next(i for i, c in enumerate(stream) if d.feed(c))
    assert d.started
    assert stopped_at == 5 + 20 + 9  # 10 тихи парчета по 0.03 s ≈ 0.3 s


def test_gives_up_when_nobody_speaks():
    d = SilenceDetector(chunk_seconds=0.1, start_timeout_seconds=1.0, calibration_chunks=2)
    assert any(d.feed(c) for c in _chunks(0.001, 20))
    assert not d.started


def test_wav_and_language_codes():
    wav = to_wav_bytes(np.zeros(1600, dtype=np.float32), 16000)
    assert wav[:4] == b"RIFF" and len(wav) == 44 + 3200
    assert _iso_code("Bulgarian") == "bg" and _iso_code("bg") == "bg" and _iso_code(None) is None
