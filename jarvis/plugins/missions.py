"""„Работи до 6:30“: Jarvis keeps working on a goal on his own until a time, in rounds, and leaves a report.

A mission is a goal and an end time. Every few minutes a round runs as a scheduled job (a reminder with channel
"agent" whose text starts with [mission:N], so it goes on after a restart): the full agent does the next piece of
work with his tools and writes a few lines on what he really did. The notes grow round by round in a file in the
vault (output/), and at the end time he writes the report from them and tells the owner. Nothing that needs the
owner's „да“ happens unattended: those actions are collected in the report for the morning. While a mission runs,
the computer is kept from going to sleep (Windows).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from ..tools import ToolRegistry, obj

log = logging.getLogger("jarvis.missions")

MARK = re.compile(r"^\[mission:(\d+)\]\s*")
EVERY = 20  # minutes between rounds unless asked otherwise
NOTES_SHOWN = 6000  # characters of earlier notes each round sees

ROUND = """(You are working on your own while the owner is away; they are back at {until}. Round {n}.)
Goal: {goal}

Done so far (your notes from earlier rounds):
{notes}

Now do the next most useful concrete piece of work toward the goal with your tools: research, read files or code, \
run checks or tests, read or watch material, write what you learn to the vault. Don't redo finished work. Nothing \
that needs the owner's yes can happen now (it is refused): write such ideas down as proposals for the morning. \
Finish with a line starting with ROUND: followed by 2-5 short lines on what you actually did and found in this \
round: only what your tools really did."""

REPORT = """(The owner is back. Write the report of the work you did on your own.)
Goal: {goal}
From {start} to {end}, {n} rounds. Your notes:
{notes}

Waiting for the owner's yes: {waiting}

Write the report for the owner: what was done, what was found, what waits for their yes, and what you suggest \
next. Only what the notes show. Don't use tools."""

SCHEMA = """CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY, goal TEXT NOT NULL, until TEXT NOT NULL, every INTEGER NOT NULL,
    state TEXT NOT NULL, rounds INTEGER DEFAULT 0, notes TEXT DEFAULT '', waiting TEXT DEFAULT '[]',
    report TEXT, created TEXT NOT NULL
)"""

ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def parse_until(value: str, now: datetime) -> datetime:
    """'06:30' (the next time it is 06:30) or an ISO 8601 local time."""
    from .tasks import parse_time

    found = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value or "")
    if found:
        at = now.replace(hour=int(found[1]), minute=int(found[2]), second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)
    return parse_time(value)


def table(store) -> None:
    store.execute(SCHEMA)


def get(store, mission_id: int) -> dict | None:
    return next(iter(store.query("SELECT * FROM missions WHERE id=?", (mission_id,))), None)


def active(store) -> list[dict]:
    table(store)
    return store.query("SELECT * FROM missions WHERE state='active' ORDER BY id")


def schedule(store, mission: dict, at: datetime) -> None:
    from .tasks import JOB

    store.insert("reminders", text=f"[mission:{mission['id']}] {mission['goal'][:80]}", at=at.replace(microsecond=0).isoformat(),
                 channels=JOB)


def hold_awake(on: bool) -> None:
    """Keep Windows from going to sleep while a mission runs (call it from a thread that lives on)."""
    if sys.platform.startswith("win"):
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def report_path(ctx, mission: dict) -> Path:
    from .vault import ensure

    vault = ensure(Path(ctx.settings.vault).expanduser())
    name = re.sub(r'[\\/:*?"<>|\s]+', " ", mission["goal"]).strip()[:50].strip() or "задача"
    return vault / "output" / f"Работа сам {mission['created'][:10]} - {name}.md"


def write_file(ctx, mission: dict, summary: str = "") -> Path:
    path = report_path(ctx, mission)
    waiting = json.loads(mission["waiting"] or "[]")
    state = {"active": "работя", "done": "готово", "stopped": "спряно"}.get(mission["state"], mission["state"])
    lines = [f"# Работа сам: {mission['goal']}", "",
             f"От {mission['created'][11:16]} до {mission['until'][11:16]} · {mission['rounds']} кръга · {state}", ""]
    if summary:
        lines += ["## Доклад", "", summary.strip(), ""]
    if waiting:
        lines += ["## Чака твоето „да“", ""] + [f"- {w}" for w in waiting] + [""]
    lines += ["## Дневник", "", mission["notes"].strip() or "(още няма)", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_round(ctx, mission_id: int, now: datetime | None = None) -> None:
    """One round of a mission (a scheduled job); at the end time, the report instead."""
    from ..brain import BudgetReached

    store = ctx.store
    mission = get(store, mission_id)
    if mission is None or mission["state"] != "active":
        return
    now = now or datetime.now()
    until = datetime.fromisoformat(mission["until"])
    if now >= until - timedelta(minutes=1):
        finish(ctx, mission_id)
        return
    n = mission["rounds"] + 1
    waiting = json.loads(mission["waiting"] or "[]")

    def unattended(summary: str) -> bool:
        waiting.append(re.sub(r"\s+", " ", summary)[:400])
        return False

    hub = getattr(ctx, "hub", None)
    run_id = hub.track("Работа сам", f"{mission['goal'][:60]} (кръг {n})", kind="job")[0] if hub else None
    ok, out_of_money = False, False
    try:
        answer = ctx.jarvis.ask(
            ROUND.format(until=until.strftime("%H:%M"), n=n, goal=mission["goal"],
                         notes=mission["notes"][-NOTES_SHOWN:] or "(nothing yet: this is the first round)"),
            conversation=f"mission-{mission_id}-{n}", confirmer=unattended, route=False,
            on_step=hub.board.reporter(run_id) if hub else None,
        )
        text, ok = answer.split("ROUND:", 1)[-1], True
    except BudgetReached as exc:
        text, out_of_money = f"Спрях: {exc}", True
    except Exception as exc:  # a bad round (a busy free model, no internet) must not end the night's work
        log.exception("mission round failed")
        text = f"Кръгът не стана: {exc}"
    finally:
        if hub:
            hub.board.finish(run_id, ok)
    notes = (mission["notes"] + f"\n### {datetime.now():%H:%M}, кръг {n}\n{text.strip()[:1500]}\n").lstrip()
    store.execute("UPDATE missions SET rounds=?, notes=?, waiting=? WHERE id=?", (n, notes, json.dumps(waiting, ensure_ascii=False), mission_id))
    mission = get(store, mission_id)
    write_file(ctx, mission)
    if out_of_money:
        finish(ctx, mission_id)
    elif mission["state"] == "active":
        schedule(store, mission, min(datetime.now() + timedelta(minutes=mission["every"]), until))


def finish(ctx, mission_id: int, state: str = "done") -> Path | None:
    """Write the report, tell the owner, and end the mission."""
    store = ctx.store
    mission = get(store, mission_id)
    if mission is None or mission["state"] not in ("active", "stopping"):
        return None
    store.execute("UPDATE missions SET state=? WHERE id=?", ("finishing", mission_id))
    store.execute("DELETE FROM reminders WHERE fired=0 AND text LIKE ?", (f"[mission:{mission_id}]%",))
    waiting = json.loads(mission["waiting"] or "[]")
    hub = getattr(ctx, "hub", None)
    try:
        summary = ctx.jarvis.ask(
            REPORT.format(goal=mission["goal"], start=mission["created"][11:16], end=datetime.now().strftime("%H:%M"),
                          n=mission["rounds"], notes=mission["notes"][-12000:] or "(no rounds ran)",
                          waiting="; ".join(waiting) or "nothing"),
            conversation=(hub.job_conversation("Работа сам") if hub else None) or f"mission-{mission_id}-report",
            confirmer=lambda _summary: False, route=False, insist=False,
        )
    except Exception as exc:
        log.exception("mission report failed")
        summary = f"(Не успях да напиша обобщение: {exc}. Дневникът е по-долу.)"
    store.execute("UPDATE missions SET state=? WHERE id=?", (state, mission_id))
    mission = get(store, mission_id)
    path = write_file(ctx, mission, summary)
    store.execute("UPDATE missions SET report=? WHERE id=?", (str(path), mission_id))
    ctx.notify(f"Докладът от работата сам („{mission['goal'][:60]}“) е готов: {path}\n{summary.strip()[:300]}")
    return path


def register(registry: ToolRegistry, ctx) -> None:
    store = ctx.store
    table(store)

    def owner_only() -> None:
        from ..brain import TURN

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner can leave Jarvis working on his own.")

    @registry.tool(
        "Keep working on a goal on your own until a time, e.g. while the owner sleeps ('work until 6:30 on X and "
        "give me a report'). You then really work in rounds every few minutes with all your tools until that time; "
        "your notes and the final report go to a file in the vault (output/) and the owner is told at the end. "
        "Anything that needs the owner's yes is collected in the report instead. Afterwards tell the owner exactly "
        "what you started: the goal, until when, and where the report will be.",
        obj({
            "goal": ("string", "What to work on, self-contained and specific (the rounds see only this and their notes)"),
            "until": ("string", "End time: 06:30, or ISO 8601 local time"),
            "every_minutes?": ("integer", f"Minutes between rounds (default {EVERY}, 5-120)"),
        }),
    )
    def work_until(goal: str, until: str, every_minutes: int | None = None):
        owner_only()
        now = datetime.now().replace(microsecond=0)
        end = parse_until(until, now)
        if end <= now + timedelta(minutes=5) or end > now + timedelta(hours=24):
            raise ValueError("The end time must be between 5 minutes and 24 hours from now.")
        every = min(max(int(every_minutes or EVERY), 5), 120)
        mission_id = store.insert("missions", goal=goal.strip(), until=end.isoformat(), every=every, state="active")
        mission = get(store, mission_id)
        schedule(store, mission, now)  # the first round starts now
        path = write_file(ctx, mission)
        return (f"Mission {mission_id} started: working on it now and every {every} minutes until {end:%H:%M}. "
                f"Notes and the report: {path}. The owner gets the report at {end:%H:%M}; actions that need their yes "
                "wait in it. The computer is kept awake meanwhile (it must stay plugged in, with the lid open).")

    @registry.tool("What you are working on by yourself right now (work_until), and how far it got.", obj({}))
    def work_status():
        running = active(store)
        if not running:
            return "Не работя по нищо сам в момента."
        out = []
        for m in running:
            nxt = store.query("SELECT at FROM reminders WHERE fired=0 AND text LIKE ? ORDER BY at LIMIT 1", (f"[mission:{m['id']}]%",))
            out.append(f"{m['id']}. {m['goal']}: до {m['until'][11:16]}, {m['rounds']} кръга"
                       + (f", следващ в {nxt[0]['at'][11:16]}" if nxt else "") + f". Бележки: {report_path(ctx, m)}")
        return "\n".join(out)

    @registry.tool(
        "Stop working on your own (work_until) now and write the report.",
        obj({"mission_id?": ("integer", "Which one (default: all)")}),
    )
    def stop_work(mission_id: int | None = None):
        owner_only()
        running = [m for m in active(store) if mission_id in (None, m["id"])]
        if not running:
            return "Не работя по нищо сам в момента."
        for m in running:
            store.execute("UPDATE missions SET state='stopping' WHERE id=?", (m["id"],))
            threading.Thread(target=finish, args=(ctx, m["id"], "stopped"), name="jarvis-mission-report", daemon=True).start()
        return "Спирам. Докладът ще е готов след малко: " + ", ".join(str(report_path(ctx, m)) for m in running)
