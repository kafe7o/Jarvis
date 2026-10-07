"""The hub: one HTTP server that lets every device reach Jarvis.

- A web app (chat + voice) at ``/`` for phones, tablets, TVs and other computers' browsers.
- A small JSON API for that app (ask, confirm, live notifications).
- A node API: other computers run ``jarvis node`` and Jarvis can then use their shell, files,
  screen and browser as tools named ``<device>__<tool>``.

Every request needs JARVIS_WEB_TOKEN. To reach it from outside your home network, put it
behind HTTPS, e.g. Tailscale (simplest) or a reverse proxy.
"""

from __future__ import annotations

import hmac
import itertools
import json
import logging
import re
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .tools import Tool

log = logging.getLogger("jarvis.hub")

WEB_PAGE = Path(__file__).parent / "web" / "index.html"
POLL_SECONDS = 25


class EventLog:
    """Notifications and confirmation requests for web clients (long-polled)."""

    def __init__(self, keep: int = 200):
        self.keep = keep
        self.events: list[dict] = []
        self._ids = itertools.count(1)
        self._cond = threading.Condition()

    def add(self, kind: str, **data) -> int:
        with self._cond:
            event = {"id": next(self._ids), "kind": kind, "time": time.time(), **data}
            self.events = (self.events + [event])[-self.keep:]
            self._cond.notify_all()
            return event["id"]

    def after(self, last_id: int, wait: float = POLL_SECONDS) -> list[dict]:
        deadline = time.time() + wait
        with self._cond:
            while True:
                fresh = [e for e in self.events if e["id"] > last_id]
                remaining = deadline - time.time()
                if fresh or remaining <= 0:
                    return fresh
                self._cond.wait(remaining)


class WebConfirmer:
    """Asks for confirmation in every open web client; the first answer wins."""

    def __init__(self, events: EventLog, timeout: float = 600):
        self.events = events
        self.timeout = timeout
        self.pending: dict[int, Future] = {}

    def __call__(self, summary: str) -> bool:
        fut: Future = Future()
        cid = self.events.add("confirm", text=summary)
        self.pending[cid] = fut
        try:
            return bool(fut.result(timeout=self.timeout))
        except Exception:
            return False
        finally:
            self.pending.pop(cid, None)
            self.events.add("confirm_closed", confirm_id=cid)

    def answer(self, confirm_id: int, yes: bool) -> bool:
        fut = self.pending.get(confirm_id)
        if fut and not fut.done():
            fut.set_result(yes)
            return True
        return False


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


class Hub:
    def __init__(self, ctx, token: str, port: int = 8770):
        if not token:
            raise ValueError("JARVIS_WEB_TOKEN must be set")
        self.ctx = ctx
        self.token = token
        self.port = port
        self.events = EventLog()
        self.confirmer = WebConfirmer(self.events)
        self.devices = DeviceHub(ctx.jarvis.registry)
        self.busy = threading.Lock()
        ctx.hub = self
        ctx.notifiers.append(lambda text: self.events.add("notify", text=text))

    def authorized(self, headers, query) -> bool:
        given = headers.get("Authorization", "").removeprefix("Bearer ").strip() or (query.get("token") or [""])[0]
        return hmac.compare_digest(given.encode(), self.token.encode())

    def ask(self, text: str) -> str:
        with self.busy:
            return self.ctx.jarvis.ask(text, conversation="web", confirmer=self.confirmer)

    def handle(self, method: str, path: str, query: dict, body: dict):
        """Returns (status, payload). Payload is a dict (JSON) or str (HTML)."""
        if method == "GET" and path in ("/", "/index.html"):
            return 200, WEB_PAGE.read_text(encoding="utf-8")
        if method == "GET" and path == "/api/events":
            after = int((query.get("after") or ["0"])[0])
            if after < 0:  # a client starting up only wants new events
                return 200, {"events": [], "last": self.events.events[-1]["id"] if self.events.events else 0}
            return 200, {"events": self.events.after(after)}
        if method == "POST" and path == "/api/ask":
            return 200, {"answer": self.ask(body["text"])}
        if method == "POST" and path == "/api/confirm":
            return 200, {"ok": self.confirmer.answer(int(body["id"]), bool(body["yes"]))}
        if method == "GET" and path == "/api/devices":
            return 200, {"devices": self.devices.status()}
        if method == "POST" and path == "/api/node/hello":
            return 200, {"device": self.devices.hello(body["name"], body["tools"])}
        if method == "GET" and path == "/api/node/poll":
            return 200, self.devices.poll(query["name"][0])
        if method == "POST" and path == "/api/node/result":
            self.devices.result(body["name"], int(body["job_id"]), body["result"], bool(body["is_error"]))
            return 200, {"ok": True}
        return 404, {"error": "not found"}

    def serve(self) -> ThreadingHTTPServer:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def _respond(self, status: int, payload) -> None:
                data = payload.encode() if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8" if isinstance(payload, str) else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _dispatch(self, method: str) -> None:
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if not hub.authorized(self.headers, query):
                    self._respond(401, {"error": "bad token"})
                    return
                body = {}
                if method == "POST":
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length) or b"{}")
                try:
                    self._respond(*hub.handle(method, url.path, query, body))
                except Exception as exc:
                    log.exception("hub request failed")
                    self._respond(500, {"error": f"{type(exc).__name__}: {exc}"})

            def do_GET(self):  # noqa: N802
                self._dispatch("GET")

            def do_POST(self):  # noqa: N802
                self._dispatch("POST")

            def log_message(self, fmt, *args):
                log.debug(fmt, *args)

        server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        threading.Thread(target=server.serve_forever, name="jarvis-hub", daemon=True).start()
        log.info("hub on :%s", self.port)
        return server
