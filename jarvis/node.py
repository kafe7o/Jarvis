"""Run on another computer (laptop, work PC, media PC) to let the main Jarvis use it.

    jarvis node --hub http://<main-jarvis>:8770 --name laptop

Needs JARVIS_WEB_TOKEN (same as the hub). Jarvis then gets this machine's tools as
``laptop__run_shell``, ``laptop__look_at_screen``, ``laptop__web_browser`` and so on.
Confirmation for risky tools happens on the main Jarvis before a job is sent here.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .plugins import Context, load_all
from .store import Store
from .tools import ToolRegistry

log = logging.getLogger("jarvis.node")

NODE_PLUGINS = ["system", "browser"]


class NodeClient:
    def __init__(self, hub: str, name: str, token: str):
        self.hub = hub.rstrip("/")
        self.name = name
        self.token = token
        settings = Settings()
        self.ctx = Context(settings=settings, store=Store(settings.db_path))
        self.registry = ToolRegistry()
        load_all(self.registry, self.ctx, NODE_PLUGINS)

    def request(self, path: str, body: dict | None = None, timeout: float = 60) -> dict:
        req = urllib.request.Request(
            self.hub + path,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def hello(self) -> None:
        tools = [
            {**t.definition(), "confirm": t.confirm, "local": t.local}
            for t in self.registry.tools.values()
        ]
        self.request("/api/node/hello", {"name": self.name, "tools": tools})
        print(f"Свързан с Jarvis като „{self.name}“ ({len(tools)} инструмента).")

    def run_job(self, job: dict) -> None:
        # Approval already happened on the main Jarvis; run as asked.
        result, is_error = self.registry.run(job["tool"], job["args"], confirmer=lambda _s: True, trust_local=True)
        self.request("/api/node/result", {"name": self.name, "job_id": job["job_id"], "result": result, "is_error": is_error})

    def run_forever(self) -> None:
        connected = False
        while True:
            try:
                if not connected:
                    self.hello()
                    connected = True
                job = self.request("/api/node/poll?" + urllib.parse.urlencode({"name": self.name}), timeout=60)
                if job.get("reconnect"):
                    connected = False
                elif job.get("job_id"):
                    self.run_job(job)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                log.warning("hub unreachable (%s); retrying", exc)
                connected = False
                time.sleep(5)


def run(hub: str, name: str) -> None:
    token = os.environ.get("JARVIS_WEB_TOKEN", "")
    if not token:
        raise SystemExit("Set JARVIS_WEB_TOKEN to the same value as on the main Jarvis.")
    NodeClient(hub, name, token).run_forever()
