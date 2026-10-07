"""Command line: python -m jarvis [chat|voice|telegram|ask "..."|serve|check]."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import settings


def check() -> None:
    """Show which capabilities are ready and which keys are missing."""
    groups = {
        "Мозък (Claude)": ["ANTHROPIC_API_KEY"],
        "Обаждания и SMS (Twilio)": ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "JARVIS_OWNER_PHONE"],
        "Разговори по телефона (вх./изх.)": ["JARVIS_PUBLIC_URL"],
        "Плащания (Stripe)": ["STRIPE_API_KEY"],
        "Имейл": ["JARVIS_SMTP_HOST", "JARVIS_IMAP_HOST", "JARVIS_EMAIL_USER", "JARVIS_EMAIL_PASSWORD"],
        "Telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"],
    }
    for name, keys in groups.items():
        missing = [k for k in keys if not os.environ.get(k)]
        print(f"{'✅' if not missing else '❌'} {name}" + (f"  — липсва: {', '.join(missing)}" if missing else ""))
    for name, module in [("Глас", "faster_whisper"), ("Микрофон", "sounddevice"), ("Говор", "edge_tts"),
                         ("Wake word модел", "openwakeword"), ("Екран/клавиатура", "pyautogui")]:
        try:
            __import__(module)
            print(f"✅ {name}")
        except Exception:
            print(f"❌ {name}  — pip install -e .[voice,desktop]")
    print(f"Данни: {settings.home}")


def serve() -> None:
    """Run every background service: reminders, phone webhooks, Telegram (if configured), voice (if available)."""
    import threading

    if settings.telegram_token and settings.telegram_owner_id:
        from .interfaces import telegram

        threading.Thread(target=telegram.run, name="jarvis-telegram", daemon=True).start()
    try:
        from .interfaces import voice

        voice.run()
    except ImportError:
        from .interfaces import cli

        cli.chat()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. — личен AI асистент")
    parser.add_argument("mode", nargs="?", default="chat", choices=["chat", "voice", "telegram", "ask", "serve", "check"])
    parser.add_argument("text", nargs="*", help="Въпрос за режим ask")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(asctime)s %(name)s: %(message)s")

    if args.mode == "check":
        check()
    elif args.mode == "ask":
        from .interfaces import cli

        cli.ask_once(" ".join(args.text) or sys.stdin.read())
    elif args.mode == "voice":
        from .interfaces import voice

        voice.run()
    elif args.mode == "telegram":
        from .interfaces import telegram

        telegram.run()
    elif args.mode == "serve":
        serve()
    else:
        from .interfaces import cli

        cli.chat()


if __name__ == "__main__":
    main()
