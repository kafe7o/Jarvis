"""Гласов режим на Jarvis. Стартира се с: python -m jarvis.voice"""

from __future__ import annotations

import argparse
import os
import sys

from jarvis.voice.audio import Microphone
from jarvis.voice.brain import load_responder
from jarvis.voice.config import VoiceConfig
from jarvis.voice.session import VoiceSession
from jarvis.voice.stt import create_stt
from jarvis.voice.tts import create_tts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m jarvis.voice", description="Говорете с Jarvis.")
    p.add_argument("--language", help="език на говорене: bg, en, ... или auto")
    p.add_argument("--stt", choices=["local", "openai"], help="разпознаване на реч")
    p.add_argument("--whisper-model", help="tiny, base, small, medium, large-v3")
    p.add_argument("--tts", choices=["edge", "offline"], help="синтез на реч")
    p.add_argument("--voice", help="конкретен глас, напр. bg-BG-KalinaNeural")
    p.add_argument("--keyboard", action="store_true",
                   help="пишете вместо да говорите (Jarvis пак отговаря на глас)")
    p.add_argument("--mute", action="store_true", help="без говор, само текст")
    p.add_argument("--brain", default=os.environ.get("JARVIS_BRAIN"),
                   help="друго чат ядро като module:attr (по подразбиране jarvis.assistant)")
    p.add_argument("--say", metavar="TEXT", help="само изговаря текста и излиза (проба на гласа)")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace) -> VoiceConfig:
    cfg = VoiceConfig.from_env()
    for attr, value in (("language", args.language), ("stt_backend", args.stt),
                        ("whisper_model", args.whisper_model), ("tts_backend", args.tts),
                        ("voice", args.voice)):
        if value:
            setattr(cfg, attr, value)
    return cfg


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = build_config(args)
    tts = None if args.mute else create_tts(cfg)

    if args.say:
        VoiceSession(lambda t: t, None, tts, default_language=cfg.whisper_language or "bg").speak(args.say)
        return 0

    if not args.brain and not os.environ.get("ANTHROPIC_API_KEY"):
        print("Липсва ANTHROPIC_API_KEY. Задайте я, напр.: export ANTHROPIC_API_KEY=sk-ant-...")
        return 1

    respond = load_responder(args.brain)
    language = cfg.whisper_language or "bg"
    try:
        if args.keyboard:
            VoiceSession(respond, None, tts, default_language=language).run_keyboard()
        else:
            print(f"Зареждам Whisper ({cfg.stt_backend}, {cfg.whisper_model})...")
            stt = create_stt(cfg)
            mic = Microphone(cfg.sample_rate, cfg.silence_seconds, cfg.max_record_seconds,
                             cfg.start_timeout_seconds)
            VoiceSession(respond, stt, tts, mic, default_language=language).run_voice()
    except KeyboardInterrupt:
        print("\nДовиждане, сър.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
