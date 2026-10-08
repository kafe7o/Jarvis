"""The hub: one HTTP server that lets every device reach Jarvis.

- The Jarvis app at ``/``: sign in with an account, chats, live progress, confirmations, voice,
  settings (accounts, permissions, connected services, devices). Works in any browser and opens as
  its own window with ``jarvis app``.
- A JSON API for that app, plus ``/api/ask`` for scripts and the Siri shortcut.
- A node API: other computers run ``jarvis node`` and Jarvis can then use their shell, files,
  screen and browser as tools named ``<device>__<tool>``.

The app uses account logins (cookie sessions). Scripts, Siri and nodes use JARVIS_WEB_TOKEN as a
Bearer token and act as the owner. To reach the hub from outside your home network, put it behind
HTTPS, e.g. Tailscale.
"""

from __future__ import annotations

import hmac
import ipaddress
import itertools
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import Future
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .accounts import GROUPS, SESSION_DAYS, TOKEN_USER, Accounts, User, check_password, conversation_id
from .tools import Tool, asking

log = logging.getLogger("jarvis.hub")

WEB_DIR = Path(__file__).parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
PUBLIC_API = {"/api/me", "/api/login", "/api/setup"}
POLL_SECONDS = 25
COOKIE = "jarvis_session"

TOOL_LABELS = {
    "web_browser": "Работи в браузъра", "look_at_screen": "Гледа екрана", "control_input": "Управлява мишката и клавиатурата",
    "run_python": "Пуска код", "run_shell": "Пуска команда", "remember": "Запомня", "recall": "Спомня си",
    "search_history": "Търси в старите разговори", "make_plan": "Прави план", "update_plan_step": "Отмята стъпка от плана",
    "show_plan": "Преглежда плана", "request_approval": "Иска разрешение", "create_skill": "Учи ново умение",
    "find_files": "Търси файлове", "read_file": "Чете файл", "write_file": "Записва файл", "list_dir": "Разглежда папка",
    "open_target": "Отваря", "add_task": "Добавя задача", "list_tasks": "Преглежда задачите", "add_reminder": "Слага напомняне",
    "schedule_job": "Насрочва задача", "add_event": "Добавя в календара", "list_events": "Преглежда календара",
    "make_call": "Звъни", "agent_call": "Провежда разговор", "phone_call": "Звъни от телефона", "phone_sms": "Праща SMS",
    "send_sms": "Праща SMS", "send_email": "Праща имейл", "read_email": "Чете пощата", "gmail_search": "Търси в Gmail",
    "whatsapp_send": "Праща WhatsApp", "viber_send": "Праща Viber", "android": "Работи с телефона",
    "home_control": "Управлява дома", "home_devices": "Преглежда дома", "home_camera": "Гледа камерата",
    "switch_brain": "Сменя мозъка", "delegate": "Разпределя работата на екипа", "gmail_draft": "Пише чернова в Gmail",
    "level:1": "Ниво 1: команда без AI", "level:2": "Ниво 2: бърз модел", "level:3": "Ниво 3: пълен агент",
    "vault_search": "Търси в паметта", "vault_note": "Записва в паметта", "vault_write": "Подрежда паметта",
    "vault_read": "Чете от паметта", "play_youtube": "Пуска от YouTube", "weather": "Гледа времето",
    "media_control": "Управлява музиката", "lock_computer": "Заключва компютъра",
}
GROUP_LABELS = {key: label for key, label, _desc, _sensitive in GROUPS}


def tool_label(registry, name: str) -> str:
    if name in TOOL_LABELS:
        return TOOL_LABELS[name]
    tool = registry.tools.get(name)
    if tool and tool.group == "devices":
        return f"Работи на {name.split('__')[0]}"
    return GROUP_LABELS.get(tool.group if tool else "", "Работи")


def lan_ip() -> str:
    """This computer's address on the home network (for opening the app from a phone)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.0.1", 9))  # no packet is sent; this only picks the outgoing interface
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def friendly_error(exc: Exception) -> str:
    from .brain import BudgetReached
    from .gemini import UsedUp, next_reset

    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if isinstance(exc, UsedUp):
        back = datetime.fromtimestamp(next_reset()).strftime("%H:%M")
        return (f"Безплатните заявки към Gemini за днес свършиха на всички безплатни модели. Връщат се в {back}. "
                "За да не спирам никога, инсталирай мозъка на лаптопа (Ollama): виж „Пестене“ в Настройки > Връзки.")
    if isinstance(exc, BudgetReached):
        return ("Днешният таван за Claude е достигнат, а безплатен мозък няма. Сложи безплатен Gemini ключ "
                "(Настройки > Връзки) или вдигни тавана в „Пестене“.")
    if "ollama" in low:
        return ("Мозъкът на лаптопа (Ollama) не отговаря. Пусни Ollama или в PowerShell напиши: ollama pull qwen3:4b")
    if type(exc).__module__.startswith("google."):
        if "perday" in low.replace(" ", "").replace("_", ""):
            return ("Безплатните заявки към Gemini за днес свършиха на всички безплатни модели. "
                    "Подновяват се утре около 10:00.")
        if "resource_exhausted" in low or "429" in low:
            return "Безплатният лимит на Gemini е изчерпан за момента. Опитай пак след минута."
        if "api key" in low or "api_key" in low or "permission_denied" in low:
            return "Gemini ключът е грешен. Вземи нов от aistudio.google.com/apikey и го сложи в Настройки > Връзки."
        return f"Gemini не отговори: {exc}"
    if "credit balance" in low or "billing" in low:
        return ("Няма кредит в Claude акаунта. Безплатно: вземи ключ от aistudio.google.com/apikey и го сложи "
                "в Настройки > Връзки > Gemini. Jarvis ще мине на Gemini сам.")
    if "authentication" in low or "api key" in low or "x-api-key" in low or "api_key" in low:
        return "Claude ключът липсва или е грешен. Сложи го в Настройки > Връзки."
    if "rate" in low and "limit" in low:
        return "Claude е претоварен в момента. Опитай пак след малко."
    if "connection" in low or "timed out" in low or "timeout" in low:
        return "Няма връзка с Claude. Провери интернета."
    return f"Нещо се обърка: {text}"


class EventLog:
    """Notifications, progress and confirmation requests for web clients (long-polled).

    Each event has a ``to``: a user id, or None for every owner.
    """

    def __init__(self, keep: int = 300):
        self.keep = keep
        self.events: list[dict] = []
        self._ids = itertools.count(1)
        self._cond = threading.Condition()

    def add(self, kind: str, to: int | None = None, **data) -> int:
        with self._cond:
            event = {"id": next(self._ids), "kind": kind, "time": time.time(), "to": to, **data}
            self.events = (self.events + [event])[-self.keep:]
            self._cond.notify_all()
            return event["id"]

    @staticmethod
    def visible(event: dict, user: User | None) -> bool:
        if user is None:
            return True
        return user.is_owner if event.get("to") is None else event["to"] == user.id

    def last_id(self) -> int:
        return self.events[-1]["id"] if self.events else 0

    def after(self, last_id: int, user: User | None = None, wait: float = POLL_SECONDS) -> list[dict]:
        deadline = time.time() + wait
        with self._cond:
            while True:
                fresh = [e for e in self.events if e["id"] > last_id and self.visible(e, user)]
                remaining = deadline - time.time()
                if fresh or remaining <= 0:
                    return fresh
                self._cond.wait(remaining)


class WebConfirmer:
    """Asks for confirmation in the requesting user's open app windows; the first answer wins."""

    def __init__(self, events: EventLog, timeout: float = 600, on_request=None, on_always=None):
        self.events = events
        self.timeout = timeout
        self.on_request = on_request or (lambda _summary: None)
        self.on_always = on_always or (lambda: None)
        self.pending: dict[int, tuple[Future, int | None, bool]] = {}

    def __call__(self, summary: str, user: User | None = None, chat_id: int | None = None) -> bool:
        fut: Future = Future()
        to = user.id if user is not None and user.id else None
        info = asking.get() or {}
        can_always = bool(info.get("can_always")) and (user is None or user.is_owner)
        cid = self.events.add("confirm", to=to, text=summary, chat=chat_id, always=can_always)
        self.pending[cid] = (fut, to, can_always)
        if user is None or user.is_owner:
            try:
                self.on_request(summary)
            except Exception:
                log.exception("confirmation push failed")
        try:
            return bool(fut.result(timeout=self.timeout))
        except Exception:
            return False
        finally:
            self.pending.pop(cid, None)
            self.events.add("confirm_closed", to=to, confirm_id=cid)

    def answer(self, confirm_id: int, yes: bool, user: User | None = None, always: bool = False) -> bool:
        fut, to, can_always = self.pending.get(confirm_id, (None, None, False))
        if fut is None or fut.done():
            return False
        if user is not None and not (to == user.id or (to is None and user.is_owner)):
            return False
        if yes and always and can_always:
            self.on_always()  # stop asking for computer actions from now on
        fut.set_result(yes)
        return True


class Device:
    def __init__(self, name: str, tools: list[dict]):
        self.name = name
        self.tools = tools
        self.last_seen = time.time()
        self.jobs: list[tuple[int, str, dict]] = []
        self.results: dict[int, Future] = {}
        self.cond = threading.Condition()


def device_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")[:20]
    if not slug:
        raise ValueError("Invalid device name")
    return slug


class DeviceHub:
    """Remote computers that lend their tools to Jarvis."""

    def __init__(self, registry):
        self.registry = registry
        self.devices: dict[str, Device] = {}
        self._ids = itertools.count(1)

    def hello(self, name: str, tools: list[dict]) -> str:
        slug = device_slug(name)
        old = self.devices.get(slug)
        self.devices[slug] = dev = Device(slug, tools)
        if old:  # keep queued work across reconnects
            dev.jobs, dev.results = old.jobs, old.results
        for spec in tools:
            self._register_proxy(dev, spec)
        log.info("device %s connected with %d tools", slug, len(tools))
        return slug

    def _register_proxy(self, dev: Device, spec: dict) -> None:
        remote = spec["name"]
        prefix = f"[{dev.name}] "

        def call(**args):
            return self.call(dev.name, remote, args)

        self.registry.add(Tool(
            name=f"{dev.name}__{remote}"[:64],
            description=f"On the device '{dev.name}': {spec['description']}",
            func=call,
            input_schema=spec["input_schema"],
            confirm=spec.get("confirm", False),
            local=spec.get("local", False),
            summarize=lambda a, remote=remote: prefix + f"{remote}({json.dumps(a, ensure_ascii=False)})",
            group="devices",
        ))

    def call(self, device: str, tool: str, args: dict, timeout: float = 300):
        dev = self.devices.get(device)
        if dev is None or time.time() - dev.last_seen > 3 * POLL_SECONDS:
            raise ConnectionError(f"Device '{device}' is offline. Start `jarvis node` on it.")
        job_id = next(self._ids)
        fut: Future = Future()
        with dev.cond:
            dev.results[job_id] = fut
            dev.jobs.append((job_id, tool, args))
            dev.cond.notify_all()
        result = fut.result(timeout=timeout)
        if result.get("is_error"):
            raise RuntimeError(result["result"])
        content = result["result"]
        if isinstance(content, list):  # image blocks from a remote screenshot
            from .tools import Image
            import base64

            img = next(b for b in content if b.get("type") == "image")
            text = next((b["text"] for b in content if b.get("type") == "text"), "")
            return Image(base64.b64decode(img["source"]["data"]), img["source"]["media_type"], text)
        return content

    def poll(self, name: str) -> dict:
        dev = self.devices.get(device_slug(name))
        if dev is None:
            return {"reconnect": True}
        deadline = time.time() + POLL_SECONDS
        with dev.cond:
            while not dev.jobs and time.time() < deadline:
                dev.last_seen = time.time()
                dev.cond.wait(deadline - time.time())
            dev.last_seen = time.time()
            if dev.jobs:
                job_id, tool, args = dev.jobs.pop(0)
                return {"job_id": job_id, "tool": tool, "args": args}
        return {}

    def result(self, name: str, job_id: int, result, is_error: bool) -> None:
        dev = self.devices.get(device_slug(name))
        fut = dev and dev.results.pop(job_id, None)
        if fut:
            fut.set_result({"result": result, "is_error": is_error})

    def status(self) -> list[dict]:
        now = time.time()
        return [
            {"device": d.name, "online": now - d.last_seen < 3 * POLL_SECONDS, "tools": len(d.tools)}
            for d in self.devices.values()
        ]


IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BODY = 12_000_000  # two camera/screen frames fit with room to spare


def parse_images(items) -> list[tuple[str, str]] | None:
    """Data URLs from the app (a frame of the shared screen or the camera) -> (media type, base64)."""
    out = []
    for item in (items or [])[:2]:
        match = re.fullmatch(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)", str(item or ""))
        if not match or match[1] not in IMAGE_TYPES:
            raise HTTPError(400, "Снимката не е във формат JPEG, PNG или WebP.")
        if len(match[2]) > 5_500_000:
            raise HTTPError(413, "Снимката е твърде голяма.")
        out.append((match[1], match[2]))
    return out or None


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "SID")
EXTRA_SETTINGS = [
    ("Поведение", "", [
        ("JARVIS_TRUST_LOCAL", "Да не пита за команди, код и файлове (1 = да, 0 = не)"),
        ("JARVIS_FILE_ROOTS", "Папки, до които има достъп (C:\\ = целият диск)"),
        ("JARVIS_MODEL", "Мозък: gemini-3.5-flash-lite (безплатно), local (на лаптопа, без лимит), claude-opus-5-5, claude-fable-5-1, claude-sonnet-5-5, claude-haiku-5-5"),
        ("JARVIS_EFFORT", "Колко да мисли: low, medium, high, xhigh, max"),
        ("JARVIS_AUTO_UPDATE", "Да се обновява сам, когато има нова версия (1 = да, 0 = не)"),
        ("JARVIS_CITY", "Твоят град, за времето (празно = по интернет връзката)"),
    ]),
    ("Пестене", "https://ollama.com/download", [
        ("JARVIS_DAILY_BUDGET", "Таван за Claude на ден, в долари (празно = 1; 0 = само безплатни мозъци; без = без таван)"),
        ("JARVIS_ROUTER", "Простите команди без AI и кратките въпроси с бърз модел (1 = да, 0 = не)"),
        ("JARVIS_FAST_MODEL", "Модел за кратките въпроси (празно = най-евтиният, безплатният Gemini)"),
        ("JARVIS_LOCAL_MODEL", "Мозък на лаптопа в Ollama, без лимит (празно = най-добрият инсталиран, напр. qwen3:4b)"),
        ("JARVIS_VAULT", "Папка-памет за бележки (празно = Документи\\Jarvis Vault)"),
    ]),
    ("Глас", "https://elevenlabs.io/app/settings/api-keys", [
        ("JARVIS_TTS_VOICE", "Безплатен глас на български (bg-BG-BorislavNeural)"),
        ("ELEVENLABS_API_KEY", "ElevenLabs ключ, за глас като във филма или твой клониран глас (по желание)"),
        ("ELEVENLABS_VOICE_ID", "ElevenLabs глас (празно = George, британски мъжки)"),
    ]),
]


def is_secret(key: str) -> bool:
    return any(h in key for h in SECRET_HINTS)


class Request:
    def __init__(self, method: str, path: str, query: dict, body: dict, user: User | None, via: str, local: bool):
        self.method, self.path, self.query, self.body = method, path, query, body
        self.user, self.via, self.local = user, via, local
        self.set_cookie: str | None = None


class Hub:
    def __init__(self, ctx, token: str, port: int = 8770, host: str = "0.0.0.0", restartable: bool = False):
        if not token:
            raise ValueError("JARVIS_WEB_TOKEN must be set")
        self.ctx = ctx
        self.token = token
        self.port = port
        self.host = host
        self.restartable = restartable
        self.accounts = Accounts(ctx.store)
        self.events = EventLog()
        from .plugins.messaging import push_notify

        # With the phone in a pocket, a push says a confirmation is waiting in the app.
        self.confirmer = WebConfirmer(self.events, on_request=lambda s: push_notify(f"Чака потвърждение: {s}", "Jarvis"),
                                      on_always=lambda: self.save_settings({"JARVIS_TRUST_LOCAL": "1"}))
        self.devices = DeviceHub(ctx.jarvis.registry)
        self.chat_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self.busy = 0  # requests being answered right now; updates wait for 0
        self.running: dict[int, dict] = {}  # what Jarvis is doing right now, for Settings > Activity
        self.run_ids = itertools.count(1)
        self.updating = threading.Lock()
        self.failures: dict[str, list[float]] = defaultdict(list)
        ctx.hub = self
        ctx.notifiers.append(lambda text: self.events.add("notify", text=text))

    # identity
    def token_ok(self, given: str) -> bool:
        return bool(given) and hmac.compare_digest(given.encode(), self.token.encode())

    def token_user(self) -> User:
        return self.accounts.owner() or TOKEN_USER

    def identify(self, headers, query) -> tuple[User | None, str]:
        bearer = headers.get("Authorization", "").removeprefix("Bearer ").strip() or (query.get("token") or [""])[0]
        if bearer and self.token_ok(bearer):
            return self.token_user(), "token"
        cookie = SimpleCookie(headers.get("Cookie", ""))
        if COOKIE in cookie:
            user = self.accounts.session_user(cookie[COOKIE].value)
            if user:
                return user, "cookie"
        return None, ""

    def session_cookie(self, token: str, max_age: int = SESSION_DAYS * 86400) -> str:
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"

    def throttle(self, ip: str) -> None:
        recent = [t for t in self.failures[ip] if t > time.time() - 900]
        self.failures[ip] = recent
        if len(recent) >= 8:
            raise HTTPError(429, "Твърде много грешни опити. Опитай пак след 15 минути.")

    # actions
    def ask(self, text: str, user: User | None = None, chat_id: int | None = None, images=None) -> str:
        user = user or self.token_user()
        conversation = conversation_id(chat_id) if chat_id else ("web" if user.is_owner else f"web:{user.id}")
        to = user.id or None

        def progress(name: str) -> None:
            run["step"] = tool_label(self.ctx.jarvis.registry, name)
            self.events.add("progress", to=to, chat=chat_id, tool=name, label=run["step"])

        with self.chat_locks[chat_id or 0]:
            self.busy += 1
            run_id, run = self.track(user.name, text)
            model = self.ctx.settings.model
            try:
                return self.ctx.jarvis.ask(
                    text, conversation=conversation, user=user, on_progress=progress,
                    confirmer=lambda summary: self.confirmer(summary, user, chat_id), images=images,
                )
            finally:
                self.busy -= 1
                self.running.pop(run_id, None)
                if self.ctx.settings.model != model and self.ctx.settings.model.startswith("gemini"):
                    # Claude ran out of credit and Gemini took over: tell the app (colour, toast).
                    self.events.add("brain", brain="gemini", name="Gemini (безплатно)")

    def track(self, who: str, text: str) -> tuple[int, dict]:
        """Note a piece of work in progress (shown in Settings > Activity until it is done)."""
        run_id = next(self.run_ids)
        self.running[run_id] = {"who": who, "text": text[:200], "since": time.time(), "step": ""}
        return run_id, self.running[run_id]

    def job_conversation(self, title: str) -> str | None:
        """The owner's chat named ``title`` (made if missing), where a routine writes its result."""
        owner = self.accounts.owner()
        if owner is None:
            return None
        chat = next((c for c in self.accounts.chats(owner.id) if c["title"] == title), None)
        chat = chat or self.accounts.create_chat(owner.id, title)
        self.accounts.touch_chat(chat["id"])
        self.events.add("chats", to=owner.id)
        return conversation_id(chat["id"])

    # updates
    def update_now(self, wait_idle: bool = False) -> bool:
        """Install a newer Jarvis from GitHub and restart. Returns False when already up to date."""
        from . import updater

        if not self.updating.acquire(blocking=False):
            return True  # an update is already running
        try:
            archive = updater.newer(updater.project_root())
            if archive is None:
                return False
            while wait_idle and self.busy:
                time.sleep(30)
            self.events.add("notify", text="Инсталирам новата версия на Jarvis и се рестартирам…")
            updater.install(updater.project_root(), archive, say=log.info)
        except BaseException as exc:  # SystemExit from the updater, network errors
            log.warning("update failed: %s", exc)
            raise RuntimeError(str(exc) or "Обновяването не стана.") from None
        finally:
            self.updating.release()
        self.restart()
        return True

    def auto_update(self, first_wait: float = 120, every: float = 6 * 3600) -> None:
        """Check GitHub now and then; install a new version by itself while nobody is waiting on Jarvis."""
        def loop():
            time.sleep(first_wait)
            while True:
                if os.environ.get("JARVIS_AUTO_UPDATE", "1").strip() not in ("0", "false", "no", "не"):
                    try:
                        self.update_now(wait_idle=True)
                    except Exception as exc:
                        log.info("auto update skipped: %s", exc)
                time.sleep(every)

        threading.Thread(target=loop, name="jarvis-auto-update", daemon=True).start()

    def settings_view(self) -> list[dict]:
        from .setup_wizard import STEPS, env_path, read_env

        stored = read_env(env_path())
        sections = []
        for title, where, fields in STEPS + EXTRA_SETTINGS:
            items = []
            for key, label in fields:
                value = stored.get(key) or os.environ.get(key, "")
                secret = is_secret(key)
                items.append({"key": key, "label": label, "secret": secret, "set": bool(value),
                              "value": "" if secret else value, "hint": (value[:4] + "…") if secret and value else ""})
            ready = all(i["set"] for i in items)
            sections.append({"title": title, "where": where, "fields": items, "ready": ready})
        return sections

    def save_settings(self, values: dict) -> None:
        import dataclasses

        from .config import Settings
        from .setup_wizard import STEPS, env_path, read_env, write_env

        known = {key for _t, _w, fields in STEPS + EXTRA_SETTINGS for key, _l in fields}
        path = env_path()
        stored = read_env(path)
        before = Settings()
        for key, value in values.items():
            if key not in known or not isinstance(value, str) or "\n" in value:
                continue
            stored[key] = value.strip()
            if value.strip():
                os.environ[key] = value.strip()
            else:
                os.environ.pop(key, None)
        write_env(path, stored)
        # Apply what changed at once (speed, trust, folders); keys new plugins need still want a restart.
        after, current = Settings(), getattr(self.ctx, "settings", None)
        for f in dataclasses.fields(Settings) if current is not None else ():
            if getattr(before, f.name) != getattr(after, f.name):
                setattr(current, f.name, getattr(after, f.name))

    def restart(self) -> None:
        args = [sys.executable, "-m", "jarvis", *sys.argv[1:]]
        if "app" in args and "--no-window" not in args:
            args.append("--no-window")
        flags = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW

        def go():
            time.sleep(0.5)
            subprocess.Popen(args, cwd=os.getcwd(), creationflags=flags)
            os._exit(0)

        threading.Thread(target=go, daemon=True).start()

    # routing
    def handle(self, req: Request):
        """Returns (status, payload). Payload is a dict (JSON)."""
        m, p, b, user = req.method, req.path, req.body, req.user

        if p == "/api/me" and m == "GET":
            from .plugins.brain_switch import brain_key

            return 200, {"user": user.public() if user else None, "needs_setup": self.accounts.count() == 0,
                         "brain": brain_key(self.ctx.settings.model),
                         "can_setup": req.local or req.via == "token", "restartable": self.restartable,
                         "groups": [{"key": k, "label": l, "desc": d, "sensitive": s} for k, l, d, s in GROUPS]}
        if p == "/api/setup" and m == "POST":
            if self.accounts.count():
                raise HTTPError(409, "Вече има собственик. Влез с акаунта си.")
            if not (req.local or req.via == "token"):
                raise HTTPError(403, "Първия акаунт може да се създаде само от компютъра, на който е Jarvis.")
            self.accounts.create(b.get("email", ""), b.get("name", ""), b.get("password", ""), role="owner")
            return self._login(req, b.get("email", ""), b.get("password", ""))
        if p == "/api/login" and m == "POST":
            return self._login(req, b.get("email", ""), b.get("password", ""))

        if user is None:
            raise HTTPError(401, "Влез в акаунта си.")

        if p.startswith("/api/node/"):
            if req.via != "token":
                raise HTTPError(403, "Само за jarvis node.")
            if p == "/api/node/hello" and m == "POST":
                return 200, {"device": self.devices.hello(b["name"], b["tools"])}
            if p == "/api/node/poll" and m == "GET":
                return 200, self.devices.poll(req.query["name"][0])
            if p == "/api/node/result" and m == "POST":
                self.devices.result(b["name"], int(b["job_id"]), b["result"], bool(b["is_error"]))
                return 200, {"ok": True}

        if p == "/api/logout" and m == "POST":
            req.set_cookie = self.session_cookie("", 0)
            return 200, {"ok": True}
        if p == "/api/me" and m == "POST":
            if not user.is_owner and (b.get("password") or b.get("email")):
                raise HTTPError(403, "Имейла и паролата ти ги сменя собственикът.")
            if b.get("password") or b.get("email"):
                if user.id == 0 or not check_password(b.get("old_password", ""), self.accounts.pw_hash(user.id)):
                    raise HTTPError(400, "Сегашната парола не е вярна.")
            updated = self.accounts.update(user.id, name=b.get("name"), email=b.get("email"),
                                           password=b.get("password") or None)
            if b.get("password"):
                _u, token = self.accounts.login(updated.username, b["password"])
                req.set_cookie = self.session_cookie(token)
            return 200, {"user": updated.public()}
        if p == "/api/events" and m == "GET":
            after = int((req.query.get("after") or ["0"])[0])
            if after < 0:  # a client starting up only wants new events
                return 200, {"events": [], "last": self.events.last_id()}
            return 200, {"events": self.events.after(after, user)}
        if p == "/api/weather" and m == "GET":  # for the greeting in Jarvis mode
            from .plugins.daily import short_weather

            return 200, {"text": short_weather()}
        if p == "/api/confirm" and m == "POST":
            return 200, {"ok": self.confirmer.answer(int(b["id"]), bool(b["yes"]), user, bool(b.get("always")))}
        if p == "/api/ask" and m == "POST":
            try:
                return 200, {"answer": self.ask(b["text"], user)}
            except Exception as exc:
                log.exception("ask failed")
                return 200, {"answer": friendly_error(exc), "error": True}

        if p == "/api/chats":
            if m == "GET":
                return 200, {"chats": self.accounts.chats(user.id)}
            return 200, {"chat": self.accounts.create_chat(user.id, b.get("title") or "Нов разговор")}
        match = re.fullmatch(r"/api/chats/(\d+)(?:/(ask|rename|delete))?", p)
        if match:
            chat_id, action = int(match[1]), match[2]
            chat = self.accounts.chat(user.id, chat_id)
            if chat is None:
                raise HTTPError(404, "Няма такъв разговор.")
            if action is None and m == "GET":
                return 200, {"chat": chat, "messages": self.accounts.messages(chat_id)}
            if action == "rename" and m == "POST":
                self.accounts.rename_chat(user.id, chat_id, b.get("title", ""))
                return 200, {"ok": True}
            if action == "delete" and m == "POST":
                self.accounts.delete_chat(user.id, chat_id)
                return 200, {"ok": True}
            if action == "ask" and m == "POST":
                text = (b.get("text") or "").strip()
                if not text:
                    raise HTTPError(400, "Празно съобщение.")
                images = parse_images(b.get("images"))
                if chat["title"] == "Нов разговор":
                    title = re.sub(r"\s+", " ", text)
                    self.accounts.rename_chat(user.id, chat_id, title[:48] + ("…" if len(title) > 48 else ""))
                try:
                    answer, error = self.ask(text, user, chat_id, images), False
                except Exception as exc:
                    log.exception("ask failed")
                    answer, error = friendly_error(exc), True
                self.accounts.touch_chat(chat_id)
                return 200, {"answer": answer, "error": error, "chat": self.accounts.chat(user.id, chat_id)}

        if p == "/api/tts" and m == "POST":
            from .tts import synthesize

            try:
                audio = synthesize(str(b.get("text", "")), self.ctx.settings.tts_voice, str(b.get("sample") or "")[:3000] or None)
                return 200, (audio, "audio/mpeg")
            except ValueError:
                raise
            except Exception as exc:  # no voice engine installed or offline: the app uses the browser's voice
                raise HTTPError(503, f"Гласът не е наличен: {exc}")

        if not user.is_owner:
            raise HTTPError(403, "Само собственикът може да прави това.")

        if p == "/api/users":
            if m == "GET":
                return 200, {"users": [u.public() for u in self.accounts.list()]}
            created = self.accounts.create(b.get("email", ""), b.get("name", ""), b.get("password", ""),
                                           b.get("role", "member"))
            return 200, {"user": created.public()}
        match = re.fullmatch(r"/api/users/(\d+)(/delete)?", p)
        if match and m == "POST":
            uid = int(match[1])
            if match[2]:
                if uid == user.id:
                    raise HTTPError(400, "Не можеш да изтриеш собствения си акаунт.")
                self.accounts.delete(uid)
                return 200, {"ok": True}
            updated = self.accounts.update(uid, name=b.get("name"), password=b.get("password") or None,
                                           role=b.get("role"), perms=b.get("perms"), email=b.get("email"))
            return 200, {"user": updated.public()}
        if p == "/api/settings":
            if m == "POST":
                self.save_settings(b.get("values") or {})
            return 200, {"sections": self.settings_view()}
        if p == "/api/restart" and m == "POST":
            if not self.restartable:
                raise HTTPError(400, "Рестартирай Jarvis ръчно.")
            self.restart()
            return 200, {"ok": True}
        if p == "/api/core":
            from . import core_files

            home = self.ctx.settings.home
            if m == "POST":
                for name in core_files.FILES:
                    if isinstance(b.get(name), str):
                        core_files.write(home, name, b[name])
            return 200, {"files": [{"name": n, "title": t, "hint": h, "text": core_files.read(home, n)}
                                   for n, (t, h) in core_files.FILES.items()]}
        if p == "/api/routines":
            from . import routines

            if m == "POST":
                if b.get("key") not in routines.ROUTINES:
                    raise HTTPError(400, "Няма такава рутина.")
                routines.set_routine(self.ctx.store, b["key"], bool(b.get("enabled")), b.get("time") or None)
            return 200, {"routines": routines.status(self.ctx.store)}
        if p == "/api/usage" and m == "GET":
            from .usage import summary

            return 200, summary(self.ctx.store, getattr(getattr(self.ctx.jarvis, "gemini", None), "spent", None))
        if p == "/api/now" and m == "GET":  # what Jarvis is doing now and what it will do on its own
            from .plugins.tasks import JOB
            from .routines import routine_of, strip_marker

            upcoming = []
            for r in self.ctx.store.query("SELECT * FROM reminders WHERE fired=0 ORDER BY at LIMIT 30"):
                routine = routine_of(r["text"])
                kind = "routine" if routine else "job" if r["channels"] == JOB else "reminder"
                upcoming.append({"at": r["at"], "repeat": r["repeat"], "kind": kind,
                                 "text": routine["title"] if routine else strip_marker(r["text"])})
            now = time.time()
            return 200, {"running": [{**r, "seconds": int(now - r["since"])} for r in list(self.running.values())],
                         "upcoming": upcoming}
        if p == "/api/activity" and m == "GET":
            names = {u.id: u.name for u in self.accounts.list()}
            rows = self.ctx.store.query("SELECT * FROM activity ORDER BY id DESC LIMIT 300")
            labels = self.ctx.jarvis.registry
            return 200, {"activity": [{**r, "user": names.get(r["user_id"], "Jarvis"), "label": tool_label(labels, r["tool"])}
                                      for r in rows]}
        if p == "/api/update" and m == "POST":
            if not self.restartable:
                raise HTTPError(400, "Обнови с `jarvis update` и рестартирай Jarvis.")
            try:
                done = self.update_now()
            except RuntimeError as exc:
                raise HTTPError(502, f"Не успях да обновя: {exc}")
            return 200, {"updated": done}
        if p == "/api/devices" and m == "GET":
            return 200, {"devices": self.devices.status(), "lan_url": f"http://{lan_ip()}:{self.port}/"}
        raise HTTPError(404, "not found")

    def _login(self, req: Request, email: str, password: str):
        ip = req.query.get("_ip", ["?"])[0]
        self.throttle(ip)
        result = self.accounts.login(email, password)
        if not result:
            self.failures[ip].append(time.time())
            time.sleep(0.5)
            raise HTTPError(401, "Грешен имейл или парола.")
        user, token = result
        req.set_cookie = self.session_cookie(token)
        return 200, {"user": user.public()}

    def serve(self) -> ThreadingHTTPServer:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self, status: int, payload, content_type: str | None = None, cookie: str | None = None) -> None:
                if isinstance(payload, (bytes, str)):
                    data = payload if isinstance(payload, bytes) else payload.encode()
                else:
                    data = json.dumps(payload, ensure_ascii=False).encode()
                    content_type = "application/json; charset=utf-8"
                self.send_response(status)
                self.send_header("Content-Type", content_type or "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                if cookie is not None:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                self.wfile.write(data)

            def _dispatch(self, method: str) -> None:
                url = urlparse(self.path)
                if method == "GET" and url.path in STATIC:
                    name, ctype = STATIC[url.path]
                    self._respond(200, (WEB_DIR / name).read_bytes(), ctype)
                    return
                if not url.path.startswith("/api/"):
                    self._respond(404, {"error": "not found"})
                    return
                query = parse_qs(url.query)
                ip = self.client_address[0]
                try:
                    local = ipaddress.ip_address(ip).is_loopback
                except ValueError:
                    local = False
                query["_ip"] = [ip]
                user, via = hub.identify(self.headers, query)
                try:
                    if method == "POST" and via != "token" and self.headers.get("X-Jarvis") != "1":
                        raise HTTPError(403, "Missing X-Jarvis header.")  # blocks cross-site form posts
                    if user is None and url.path not in PUBLIC_API:
                        raise HTTPError(401, "Влез в акаунта си.")
                    body = {}
                    if method == "POST":
                        length = int(self.headers.get("Content-Length", 0))
                        if length > MAX_BODY:
                            self.close_connection = True  # the unread body must not be parsed as a request
                            raise HTTPError(413, "Заявката е твърде голяма.")
                        body = json.loads(self.rfile.read(length) or b"{}")
                    req = Request(method, url.path, query, body, user, via, local)
                    status, payload = hub.handle(req)
                    if isinstance(payload, tuple):  # (bytes, content type), e.g. speech audio
                        self._respond(status, payload[0], payload[1], cookie=req.set_cookie)
                    else:
                        self._respond(status, payload, cookie=req.set_cookie)
                except HTTPError as exc:
                    self._respond(exc.status, {"error": str(exc)})
                except (ValueError, KeyError) as exc:
                    self._respond(400, {"error": str(exc).strip("'")})
                except Exception as exc:
                    log.exception("hub request failed")
                    self._respond(500, {"error": f"{type(exc).__name__}: {exc}"})

            def do_GET(self):  # noqa: N802
                self._dispatch("GET")

            def do_POST(self):  # noqa: N802
                self._dispatch("POST")

            def log_message(self, fmt, *args):
                log.debug(fmt, *args)

        for attempt in range(40):  # after a restart the old process may still hold the port briefly
            try:
                server = ThreadingHTTPServer((self.host, self.port), Handler)
                break
            except OSError:
                if attempt == 39:
                    raise
                time.sleep(0.25)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, name="jarvis-hub", daemon=True).start()
        log.info("hub on %s:%s", self.host, server.server_address[1])
        return server
