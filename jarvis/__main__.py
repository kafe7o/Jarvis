"""Command line: python -m jarvis [app|chat|voice|telegram|ask "..."|serve|check|setup|...]."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from contextlib import contextmanager

from .config import settings


def check() -> None:
    """Show which capabilities are ready and which keys are missing."""
    groups = {
        "Мозък (Claude, платен)": ["ANTHROPIC_API_KEY"],
        "Мозък (Gemini, безплатен)": ["GEMINI_API_KEY"],
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


@contextmanager
def recovering():
    """Starting up: if Jarvis cannot start right after he changed his own code (upgrade_self), undo that
    change and start again (upgrades.py)."""
    try:
        yield
    except Exception:
        from . import upgrades

        if upgrades.recover(settings.home) is None:
            raise
        logging.getLogger("jarvis").exception("Jarvis could not start after an upgrade; it was undone")
        flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW
        subprocess.Popen([sys.executable, "-m", "jarvis", *sys.argv[1:]], cwd=os.getcwd(), creationflags=flags)
        os._exit(1)


def undo() -> None:
    """`jarvis undo`: put back the code from before Jarvis's last upgrade of himself (when the app no longer opens)."""
    from . import upgrades
    from .updater import restart_running_app

    try:
        record = upgrades.undo(settings.home)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc))
    print(f"Върнах надстройка {record['id']}: {record['what']}")
    restart_running_app()


def serve(headless: bool = False) -> None:
    """Everything in one process sharing one Jarvis: reminders, heartbeat, phone, web hub,
    Telegram (if configured) and voice (if installed; otherwise the terminal chat)."""
    import threading

    with recovering():
        from .app import build, start_background
        from .interfaces import cli

        shared = build(cli.console_confirm, [cli.notify], on_progress=cli.progress)
        start_background(shared[1], restartable=headless)
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
    print(f"Jarvis е достъпен на http://<този-компютър>:{port}/")
    threading.Event().wait()


BROWSERS_WIN = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
]
BROWSERS_MAC = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
BROWSERS_LINUX = ["google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]


def open_window(url: str) -> None:
    """Open the app in its own window (Edge/Chrome app mode), else in the default browser."""
    import shutil
    import subprocess
    import webbrowser

    if sys.platform.startswith("win"):
        candidates = [os.path.expandvars(p) for p in BROWSERS_WIN]
    elif sys.platform == "darwin":
        candidates = BROWSERS_MAC
    else:
        candidates = [shutil.which(name) or "" for name in BROWSERS_LINUX]
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1180,820"])
            return
    webbrowser.open(url)


def hub_running(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/me", timeout=2) as resp:
            return "needs_setup" in resp.read().decode()
    except Exception:
        return False


def app(window: bool = True) -> None:
    """The Jarvis app: the hub on this computer plus a window for it. Running it again only opens
    another window."""
    import secrets
    import threading

    from .setup_wizard import env_path, read_env, write_env

    token = os.environ.get("JARVIS_WEB_TOKEN")
    if not token:  # nodes, Siri and scripts use it; the app itself uses account logins
        token = secrets.token_urlsafe(24)
        path = env_path()
        values = read_env(path)
        values["JARVIS_WEB_TOKEN"] = token
        write_env(path, values)
        os.environ["JARVIS_WEB_TOKEN"] = token
    port = int(os.environ.get("JARVIS_WEB_PORT", "8770"))
    url = f"http://localhost:{port}/"
    if hub_running(port):
        if window:
            open_window(url)
        return

    holder: dict = {}
    with recovering():
        from .app import build, start_background
        from .hub import Hub

        _jarvis, ctx = build(lambda summary: holder["hub"].confirmer(summary))
        holder["hub"] = Hub(ctx, token, port, restartable=True)
        holder["hub"].serve()
        start_background(ctx)
    if settings.telegram_token and settings.telegram_owner_id:
        from .interfaces import telegram

        threading.Thread(target=telegram.run, args=((ctx.jarvis, ctx),), name="jarvis-telegram", daemon=True).start()
    if window:
        open_window(url)
    print(f"Jarvis работи: {url}  (от телефона: http://<IP-на-този-компютър>:{port}/)")
    threading.Event().wait()


def owner() -> None:
    """Create the owner account, or reset its e-mail and password (forgotten password)."""
    from getpass import getpass

    from .accounts import Accounts
    from .store import Store

    accounts = Accounts(Store(settings.db_path))
    current = accounts.owner()
    print("Акаунт на собственика (вход в приложението Jarvis).")
    email = input("Имейл" + (f" [{current.username}]" if current else "") + ": ").strip() or (current.username if current else "")
    name = input("Как да се обръщам към теб" + (f" [{current.name}]" if current else "") + ": ").strip()
    while True:
        password = getpass("Парола (поне 6 знака, не се вижда докато пишеш): ")
        if password == getpass("Паролата отново: "):
            break
        print("Паролите не съвпадат, опитай пак.")
    try:
        user = accounts.set_owner(email, password, name)
    except ValueError as exc:
        raise SystemExit(str(exc))
    print(f"Готово. Влизаш с {user.username} и тази парола.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. — личен AI асистент")
    parser.add_argument("mode", nargs="?", default="chat", choices=["app", "owner", "shortcut", "update", "undo", "chat", "voice", "telegram", "web", "node", "ask", "serve", "daemon", "check", "setup",
                                 "enroll-voice", "google-login"])
    parser.add_argument("text", nargs="*", help="Въпрос за режим ask")
    parser.add_argument("--hub", help="node: адрес на главния Jarvis, напр. http://192.168.1.10:8770")
    parser.add_argument("--name", help="node: име на това устройство, напр. laptop")
    parser.add_argument("--no-window", action="store_true", help="app: само сървърът, без прозорец")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    level = logging.INFO if args.verbose else logging.WARNING
    if sys.stderr is None:  # pythonw (desktop icon, autostart): no console, log to a file
        settings.home.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=settings.home / "jarvis.log", level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(name)s: %(message)s")

    if args.mode == "app":
        app(window=not args.no_window)
    elif args.mode == "owner":
        owner()
    elif args.mode == "update":
        from pathlib import Path

        from .updater import update

        update(Path.cwd())
    elif args.mode == "undo":
        undo()
    elif args.mode == "shortcut":
        from pathlib import Path

        from .autostart import shortcut

        print(shortcut(Path.cwd()))
    elif args.mode == "check":
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
