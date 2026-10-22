"""Command line: python -m jarvis [app|chat|voice|telegram|ask "..."|serve|check|setup|...]."""

from __future__ import annotations

import argparse
import faulthandler
import logging
import logging.handlers
import os
import subprocess
import sys
from contextlib import contextmanager

from .config import settings

CRASH: dict = {}  # the file Python writes to if it crashes without a console (kept open while Jarvis runs)


def check() -> None:
    """Show which capabilities are ready and which keys are missing."""
    groups = {
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
    from . import local

    brain = local.pick(settings.local_model or None)
    print(f"✅ Мозък на лаптопа: {brain}" if brain else "❌ Мозък на лаптопа  — няма Ollama или модел: Настройки > Мозък")
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


def doctor() -> None:
    """`jarvis doctor`: what works, what does not and how to fix it (the run_doctor tool, from PowerShell)."""
    from . import doctor as checks
    from .app import build

    _jarvis, ctx = build(lambda summary: False)
    print(checks.report(checks.examine(ctx)))


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
        from . import keeper

        if keeper.kept():
            os._exit(keeper.RESTART)  # the keeper starts him again
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


def app(window: bool = True) -> None:
    """The Jarvis app. Run by the keeper (keeper.py) it is Jarvis himself, the hub on this computer. Run by a
    person or the desktop icon, it starts the keeper unless Jarvis already runs, waits for him and opens the
    window; that PowerShell window may then be closed."""
    import secrets
    import threading
    import time

    from . import keeper
    from .setup_wizard import env_path, read_env, write_env
    from .updater import answering

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
    if not keeper.kept():
        if not answering(port):
            keeper.start()  # (if one already runs, e.g. while it restarts Jarvis, the new one ends at once)
            print("Пускам Jarvis…", flush=True)
            end = time.time() + 120
            while not answering(port):
                if time.time() > end:
                    print(f"Jarvis още не отговаря. Какво става пише в {settings.home / 'jarvis.log'}")
                    return
                time.sleep(1)
        if window:
            open_window(url)
        print(f"Jarvis работи във фона: {url}  (от телефона: http://<IP-на-този-компютър>:{port}/)\n"
              "Този прозорец вече може да се затвори. Спиране на Jarvis: jarvis stop")
        return

    holder: dict = {}
    with recovering():
        from .app import build, start_background
        from .hub import Hub

        _jarvis, ctx = build(lambda summary: holder["hub"].confirmer(summary))
        holder["hub"] = Hub(ctx, token, port, restartable=True)
        holder["hub"].serve()
        start_background(ctx)
    from . import hotkey

    def summoned() -> None:  # Ctrl+Alt+J: the window in front (or opened), listening
        if hotkey.bring_up(open_window, url):
            holder["hub"].events.add("listen")

    hotkey.start(summoned)
    if settings.telegram_token and settings.telegram_owner_id:
        from .interfaces import telegram

        threading.Thread(target=telegram.run, args=((ctx.jarvis, ctx),), name="jarvis-telegram", daemon=True).start()
    print(f"Jarvis работи: {url}", flush=True)
    threading.Event().wait()


def stop() -> None:
    """`jarvis stop`: Jarvis stops, and his keeper does not start him again."""
    import json
    import urllib.request

    port = os.environ.get("JARVIS_WEB_PORT", "8770")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/stop", data=json.dumps({}).encode(), headers={
        "Authorization": f"Bearer {os.environ.get('JARVIS_WEB_TOKEN', '')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5):
            print("Jarvis спира.")
    except Exception:
        print("Jarvis не работи (или не отговаря).")


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


def utf8_console() -> None:
    """Cyrillic in a print must never stop Jarvis. The Jarvis the keeper runs writes to a log file through a pipe,
    which Windows opens in the system code page (cp1252 on an English Windows: the owner's new laptop), and
    "Jarvis работи" then died with UnicodeEncodeError on every start, a minute apart."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. — личен AI асистент")
    parser.add_argument("mode", nargs="?", default="chat", choices=["app", "stop", "keeper", "owner", "shortcut", "update", "undo", "chat", "voice", "telegram", "web", "node", "ask", "serve", "daemon", "check", "setup",
                                 "enroll-voice", "google-login", "doctor", "backup", "restore"])
    parser.add_argument("text", nargs="*", help="Въпрос за режим ask")
    parser.add_argument("--hub", help="node: адрес на главния Jarvis, напр. http://192.168.1.10:8770")
    parser.add_argument("--name", help="node: име на това устройство, напр. laptop")
    parser.add_argument("--no-window", action="store_true", help="app: само сървърът, без прозорец")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    level = logging.INFO if args.verbose else logging.WARNING
    settings.home.mkdir(parents=True, exist_ok=True)
    from . import keeper

    if args.mode == "keeper":
        pass  # it writes its own notes in jarvis.log
    elif keeper.kept():  # the keeper's Jarvis: what he prints already goes to jarvis.log
        logging.basicConfig(level=level, format="%(asctime)s %(name)s: %(message)s")
        faulthandler.enable()  # if Python itself crashes, where it was
    elif sys.stderr is None:  # pythonw (desktop icon, autostart): no console, log to a file
        logging.basicConfig(filename=settings.home / "jarvis.log", level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
        CRASH["file"] = open(settings.home / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(CRASH["file"])  # if Python itself crashes, where it was
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(name)s: %(message)s")
        faulthandler.enable()
        if args.mode in ("serve", "daemon", "web"):  # the warnings also stay in a file, to read after a stop
            kept = logging.handlers.RotatingFileHandler(settings.home / "jarvis.log", maxBytes=1_000_000, backupCount=1,
                                                        encoding="utf-8")
            kept.setLevel(logging.WARNING)
            kept.setFormatter(logging.Formatter("%(asctime)s %(name)s: %(message)s"))
            logging.getLogger().addHandler(kept)

    if args.mode == "app":
        app(window=not args.no_window)
    elif args.mode == "stop":
        stop()
    elif args.mode == "keeper":
        keeper.run(settings.home)
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
    elif args.mode == "doctor":
        doctor()
    elif args.mode in ("backup", "restore"):
        from pathlib import Path

        from . import backup
        from .setup_wizard import env_path

        if args.mode == "backup":
            where = backup.backup(settings.home, env_path())
            print(f"Готово: {where}\nВътре са паметта и ключовете: копирай го на флашка за новия лаптоп, но не го "
                  "пращай на никого. На новия: jarvis stop, после jarvis restore")
        else:
            archive = Path(" ".join(args.text)) if args.text else backup.newest_backup()
            if archive is None or not archive.is_file():
                print("Не намирам резервно копие. Сложи Jarvis-backup-....zip в Documents или Downloads, или напиши: "
                      "jarvis restore \"C:\\път\\до\\файла.zip\"")
            else:
                try:
                    print(backup.restore(archive, settings.home, env_path()))
                except (RuntimeError, ValueError, OSError) as exc:
                    print(exc)
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
