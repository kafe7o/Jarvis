"""Jarvis's work, live: every request, scheduled job and specialist, step by step, for the app's "На живо" screen.

The brain reports what happens (``on_step`` in brain.Jarvis.ask): which level answers, each tool call as it
starts and ends, the specialists it hands work to, and its plan. ``Board`` keeps that as a small picture of
each piece of work, current and recently finished, and tells the app about every change.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from collections import deque
from typing import Callable

MAX_STEPS = 80
OPEN = ("running", "waiting")  # a step still going: working, or waiting for the person's yes


class Board:
    def __init__(self, label: Callable[[str], str] = str, publish: Callable[[dict, int | None], None] | None = None,
                 keep: int = 10):
        self.label = label  # tool name -> words for people ("Търси в интернет")
        self.publish = publish or (lambda run, to: None)
        self.running: dict[int, dict] = {}
        self.recent: deque[dict] = deque(maxlen=keep)
        self._ids = itertools.count(1)
        self._lock = threading.RLock()

    def start(self, who: str, text: str, kind: str = "request", chat: int | None = None, to: int | None = None) -> tuple[int, dict]:
        """A new piece of work: a request someone made, or a scheduled job (kind "job")."""
        with self._lock:
            run_id = next(self._ids)
            run = {"id": run_id, "who": who, "text": text[:300], "kind": kind, "chat": chat, "to": to,
                   "since": time.time(), "ended": None, "state": "working", "step": "", "level": None,
                   "steps": [], "agents": [], "plan": None}
            self.running[run_id] = run
        self._publish(run)
        return run_id, run

    def reporter(self, run_id: int) -> Callable[[dict], None]:
        """The ``on_step`` callback for the brain: applies each event to this run and tells the app."""
        def report(event: dict) -> None:
            with self._lock:
                run = self.running.get(run_id)
                if run is None:
                    return
                self.apply(run, event)
            self._publish(run)
        return report

    def apply(self, run: dict, event: dict) -> None:
        kind, now = event.get("type"), time.time()
        if kind == "level":
            run["level"] = event.get("level")
        elif kind == "step":
            state = event.get("state", "ok")
            step = next((st for st in reversed(run["steps"]) if st["id"] == event.get("id")), None)
            if step is None and event.get("tool"):  # a new step starts
                label = self.label(event["tool"])
                run["step"] = label
                run["steps"].append({"id": event.get("id"), "agent": event.get("agent") or "Jarvis", "tool": event["tool"],
                                     "label": label, "detail": event.get("detail", ""), "state": state,
                                     "since": now, "ended": None})
                del run["steps"][:-MAX_STEPS]
            elif step is not None:
                step["state"] = state
                step["ended"] = None if state in OPEN else now
                if state == "waiting":
                    run["step"] = "Чака твоето „да“"
                elif state == "running":
                    run["step"] = step["label"]
        elif kind == "agent":
            agent = next((a for a in run["agents"] if a["name"] == event.get("agent")), None)
            if agent is None:
                agent = {"name": event.get("agent"), "task": event.get("task", "")[:300], "since": now, "ended": None}
                run["agents"].append(agent)
            agent["state"] = event.get("state", "running")
            if agent["state"] != "running":
                agent["ended"] = now
        elif kind == "plan":
            run["plan"] = event.get("plan")

    def finish(self, run_id: int, ok: bool = True) -> None:
        with self._lock:
            run = self.running.pop(run_id, None)
            if run is None:
                return
            run["state"], run["ended"] = ("done" if ok else "error"), time.time()
            for step in run["steps"]:
                if step["state"] in OPEN:  # a step that never reported back
                    step["state"], step["ended"] = ("ok" if ok else "error"), run["ended"]
            self.recent.appendleft(run)
        self._publish(run)

    def snapshot(self) -> dict:
        with self._lock:
            return {"running": [public(r) for r in self.running.values()], "recent": [public(r) for r in self.recent],
                    "t": time.time()}

    def _publish(self, run: dict) -> None:
        with self._lock:
            shown = public(run)
        try:
            self.publish(shown, run.get("to"))
        except Exception:  # the app being away must never stop the work
            pass


def public(run: dict) -> dict:
    """What the app shows of a run (copied, so it can be sent while the work goes on)."""
    return {**{k: v for k, v in run.items() if k not in ("steps", "agents", "plan")},
            "steps": [dict(s) for s in run["steps"]], "agents": [dict(a) for a in run["agents"]],
            "plan": dict(run["plan"]) if run.get("plan") else None,
            "waiting": any(st["state"] == "waiting" for st in run["steps"]),
            "seconds": int((run["ended"] or time.time()) - run["since"])}


def detail(tool, args: dict, limit: int = 160) -> str:
    """One short line on what a step works on: the tool's own summary, else its arguments."""
    try:
        if tool is not None and tool.summarize:
            text = str(tool.summarize(args))
        else:
            text = " · ".join(shown(v) for v in (args or {}).values() if v not in (None, "", [], {}) and not isinstance(v, bool))
    except Exception:
        text = ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def shown(value) -> str:
    if isinstance(value, list):
        return ", ".join(shown(v) for v in value)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
