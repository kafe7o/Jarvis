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
        "Уеб приложение и други устройства": ["JARVIS_WEB_TOKEN"],
        "Android телефон / TV": ["JARVIS_ADB_DEVICES"],
        "Умен дом (Home Assistant)": ["HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN"],
        "Google Calendar и Gmail": ["GOOGLE_CLIENT_SECRET"],
        "Плащания към други (PayPal)": ["PAYPAL_CLIENT_ID", "PAYPAL_SECRET"],
        "Банкови преводи (Wise)": ["WISE_API_TOKEN"],
        "Известия на телефона (ntfy)": ["NTFY_TOPIC"],
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


def serve(headless: bool = False) -> None:
    """Everything in one process sharing one Jarvis: reminders, heartbeat, phone, web hub,
    Telegram (if configured) and voice (if installed; otherwise the terminal chat)."""
    import threading

    from .app import build, start_background
    from .interfaces import cli

    shared = build(cli.console_confirm, [cli.notify], on_progress=cli.progress)
    start_background(shared[1])
    if settings.telegram_token and settings.telegram_owner_id:
        from .interfaces import telegram

        threading.Thread(target=telegram.run, args=(shared,), name="jarvis-telegram", daemon=True).start()
    try:
        import faster_whisper  # noqa: F401
        import sounddevice  # noqa: F401
    except ImportError:
        if headless:  # autostart service: no terminal, keep the background services alive
            threading.Event().wait()
        cli.chat(shared)
        return
    from .interfaces import voice

    voice.run(shared)


def web() -> None:
    """Only the hub: web app for phones/TVs/tablets, device nodes, reminders, phone calls."""
    import threading

    from .app import build, start_background

    if not os.environ.get("JARVIS_WEB_TOKEN"):
        raise SystemExit("Set JARVIS_WEB_TOKEN in .env first (any long random string).")
    _jarvis, ctx = build(lambda _s: False, [print])
    start_background(ctx)
    port = os.environ.get("JARVIS_WEB_PORT", "8770")
    print(f"Jarvis е достъпен на http://<този-компютър>:{port}/?token=<JARVIS_WEB_TOKEN>")
    threading.Event().wait()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. — личен AI асистент")
    parser.add_argument("mode", nargs="?", default="chat", choices=["chat", "voice", "telegram", "web", "node", "ask", "serve", "daemon", "check", "setup",
                                 "enroll-voice", "google-login"])
    parser.add_argument("text", nargs="*", help="Въпрос за режим ask")
    parser.add_argument("--hub", help="node: адрес на главния Jarvis, напр. http://192.168.1.10:8770")
    parser.add_argument("--name", help="node: име на това устройство, напр. laptop")
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
    elif args.mode == "web":
        web()
    elif args.mode == "node":
        import platform

        from .node import run as run_node

        run_node(args.hub or os.environ.get("JARVIS_HUB_URL", ""), args.name or platform.node())
    elif args.mode == "serve":
        serve()
    elif args.mode == "daemon":
        serve(headless=True)
    elif args.mode == "setup":
        from .setup_wizard import run as run_setup

        run_setup()
    elif args.mode == "enroll-voice":
        from .voice.speaker import enroll

        enroll(settings)
    elif args.mode == "google-login":
        from .plugins.google import login

        login(settings)
    else:
        from .interfaces import cli

        cli.chat()


if __name__ == "__main__":
    main()
