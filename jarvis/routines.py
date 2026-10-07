"""Ready-made routines the owner switches on in the app (Settings > Routines).

Each is a scheduled job (a reminder with channel "agent") whose text starts with a marker, so the app
knows which routine it is. Jarvis carries it out on its own and writes the result into a chat with
the routine's name; anything that needs the owner's "yes" is not done unattended.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .plugins.tasks import JOB

ROUTINES = {
    "morning": {
        "title": "Сутрешен брифинг",
        "desc": "Всяка сутрин: календар, задачи, важната поща и времето.",
        "time": "08:00", "repeat": "daily",
        "instruction": "Morning brief for the owner: today's calendar and tasks, reminders, overdue items, important "
                       "unread e-mail if mail is connected, today's weather where they live if you know it, and one "
                       "useful suggestion for the day. Short and in their language.",
    },
    "inbox": {
        "title": "Нощна поща",
        "desc": "Вечер подрежда пощата и оставя чернови за отговор. Не праща нищо.",
        "time": "22:30", "repeat": "daily",
        "instruction": "Evening inbox triage: go through today's unread e-mail, sort what matters from what does not, "
                       "save reply drafts with gmail_draft for the ones that need an answer (never send anything), "
                       "and report briefly what needs the owner tomorrow.",
    },
    "week": {
        "title": "Седмичен преглед",
        "desc": "Неделя вечер: какво стана тази седмица и какво предстои.",
        "time": "19:00", "repeat": "weekly", "weekday": 6,
        "instruction": "Weekly review for the owner: what got done this week (tasks, plans, activity), what is still "
                       "open, next week's calendar and deadlines, and the three most important things to focus on.",
    },
}
MARK = re.compile(r"^\[routine:(\w+)\]\s*")


def marker(key: str) -> str:
    return f"[routine:{key}] "


def routine_of(text: str) -> dict | None:
    match = MARK.match(text or "")
    return ROUTINES.get(match.group(1)) if match else None


def strip_marker(text: str) -> str:
    return MARK.sub("", text or "")


def _first_run(spec: dict, hhmm: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    hour, minute = (int(x) for x in hhmm.split(":"))
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "weekday" in spec:
        at += timedelta(days=(spec["weekday"] - at.weekday()) % 7)
    while at <= now:
        at += timedelta(days=7 if "weekday" in spec else 1)
    return at


def _rows(store) -> dict[str, dict]:
    rows = store.query("SELECT * FROM reminders WHERE channels=? AND fired=0 AND text LIKE '[routine:%'", (JOB,))
    return {MARK.match(r["text"]).group(1): r for r in rows if MARK.match(r["text"])}


def status(store) -> list[dict]:
    rows = _rows(store)
    out = []
    for key, spec in ROUTINES.items():
        row = rows.get(key)
        time = datetime.fromisoformat(row["at"]).strftime("%H:%M") if row else spec["time"]
        out.append({"key": key, "title": spec["title"], "desc": spec["desc"], "time": time, "enabled": bool(row)})
    return out


def set_routine(store, key: str, enabled: bool, hhmm: str | None = None) -> None:
    spec = ROUTINES[key]
    hhmm = hhmm or spec["time"]
    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", hhmm):
        raise ValueError("Часът трябва да е като 08:00.")
    store.execute("DELETE FROM reminders WHERE channels=? AND text LIKE ?", (JOB, marker(key) + "%"))
    if enabled:
        store.insert("reminders", text=marker(key) + spec["instruction"], at=_first_run(spec, hhmm).isoformat(),
                     repeat=spec["repeat"], channels=JOB)
