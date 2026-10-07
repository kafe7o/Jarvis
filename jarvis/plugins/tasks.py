"""Tasks, reminders (with a background scheduler) and a local calendar with .ics import/export."""

from __future__ import annotations

import calendar as _cal
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from ..tools import ToolRegistry, obj

log = logging.getLogger("jarvis.tasks")

REPEATS = {"hourly", "daily", "weekdays", "weekly", "monthly", "yearly"}
CHANNELS = {"local", "sms", "call", "telegram"}
JOB = "agent"  # reminders with this channel are instructions Jarvis carries out itself


def parse_time(value: str) -> datetime:
    """Parse an ISO 8601 local time such as 2026-10-08T09:30 (seconds/timezone optional)."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0)


def next_occurrence(at: datetime, repeat: str) -> datetime:
    if repeat == "hourly":
        return at + timedelta(hours=1)
    if repeat == "daily":
        return at + timedelta(days=1)
    if repeat == "weekdays":
        nxt = at + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if repeat == "weekly":
        return at + timedelta(weeks=1)
    if repeat == "monthly":
        month = at.month % 12 + 1
        year = at.year + (at.month == 12)
        day = min(at.day, _cal.monthrange(year, month)[1])
        return at.replace(year=year, month=month, day=day)
    if repeat == "yearly":
        day = min(at.day, _cal.monthrange(at.year + 1, at.month)[1])
        return at.replace(year=at.year + 1, day=day)
    raise ValueError(f"Unknown repeat '{repeat}'. Use one of: {', '.join(sorted(REPEATS))}")


HEARTBEAT_PROMPT = (
    "(Background check-in; the owner is not talking to you.) Think about what the owner might need right "
    "now. Look at open tasks, overdue items, upcoming reminders and today's and tomorrow's calendar, active "
    "plans, and unread e-mail if e-mail is configured. You may research or prepare things with read-only "
    "tools. If something deserves the owner's attention now (an overdue task, a meeting soon, an important "
    "e-mail, a useful suggestion you haven't already made in this conversation), reply with 'NOTIFY: ' "
    "followed by one or two short sentences in the owner's language. Otherwise reply exactly 'NOTHING'."
)


def in_quiet_hours(spec: str, now: datetime) -> bool:
    """spec like '23-7' (from 23:00 to 07:00). Empty means never quiet."""
    if not spec or "-" not in spec:
        return False
    start, end = (int(x) for x in spec.split("-", 1))
    return start <= now.hour or now.hour < end if start > end else start <= now.hour < end


class ReminderScheduler:
    """Background thread that fires due reminders through the requested channels."""

    def __init__(self, ctx, interval: float = 15.0):
        self.ctx = ctx
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="jarvis-reminders", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        last_heartbeat = datetime.now()
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("reminder tick failed")
            minutes = self.ctx.settings.heartbeat_minutes
            if minutes > 0 and datetime.now() - last_heartbeat >= timedelta(minutes=minutes):
                last_heartbeat = datetime.now()
                if not in_quiet_hours(self.ctx.settings.quiet_hours, last_heartbeat):
                    threading.Thread(target=self.heartbeat, name="jarvis-heartbeat", daemon=True).start()

    def heartbeat(self) -> str | None:
        """Jarvis thinks on its own: reviews the owner's situation and speaks up only when useful."""
        jarvis = getattr(self.ctx, "jarvis", None)
        if jarvis is None:
            return None
        answer = jarvis.ask(
            HEARTBEAT_PROMPT, conversation="heartbeat",
            confirmer=lambda summary: False,  # never act in the outside world unattended
        )
        message = answer.strip()
        if message.upper().startswith("NOTIFY:"):
            message = message[len("NOTIFY:"):].strip()
            self.ctx.notify(message)
            return message
        return None

    def tick(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        store = self.ctx.store
        due = store.query(
            "SELECT * FROM reminders WHERE fired=0 AND at<=? ORDER BY at", (now.replace(microsecond=0).isoformat(),)
        )
        for rem in due:
            self.deliver(rem)
            if rem["repeat"]:
                nxt = next_occurrence(parse_time(rem["at"]), rem["repeat"])
                while nxt <= now:  # skip occurrences missed while Jarvis was off
                    nxt = next_occurrence(nxt, rem["repeat"])
                store.execute("UPDATE reminders SET at=? WHERE id=?", (nxt.isoformat(), rem["id"]))
            else:
                store.execute("UPDATE reminders SET fired=1 WHERE id=?", (rem["id"],))
        return due

    def deliver(self, rem: dict) -> None:
        channels = (rem["channels"] or "local").split(",")
        if JOB in channels:
            threading.Thread(target=self.run_job, args=(rem,), name="jarvis-job", daemon=True).start()
            return
        text = f"Напомняне: {rem['text']}"
        if "local" in channels or "telegram" in channels:
            self.ctx.notify(text)
        if "sms" in channels or "call" in channels:
            from . import comms  # reminders go to the owner's own phone

            s = self.ctx.settings
            try:
                if "sms" in channels:
                    comms.send_sms_raw(s, s.owner_phone, text)
                if "call" in channels:
                    comms.call_raw(s, s.owner_phone, text)
            except Exception as exc:
                self.ctx.notify(f"{text} (не успях да звънна/пиша на телефона: {exc})")


    def run_job(self, rem: dict) -> None:
        """Carry out a scheduled instruction unattended and report the result."""
        jarvis = getattr(self.ctx, "jarvis", None)
        if jarvis is None:
            return

        from ..routines import routine_of, strip_marker

        routine = routine_of(rem["text"])
        title = routine["title"] if routine else strip_marker(rem["text"])[:60]

        def unattended(summary: str) -> bool:
            self.ctx.notify(f"„{title}“ иска одобрение за: {summary}. Не го направих; кажи ми, ако искаш.")
            return False

        # In the app, the result lands in a chat named after the job; elsewhere it has its own history.
        hub = getattr(self.ctx, "hub", None)
        conversation = (hub.job_conversation(routine["title"] if routine else "Задачи по график") if hub else None)
        run_id, run = hub.track("По график", title) if hub else (None, {})
        try:
            result = jarvis.ask(
                f"(Scheduled job, the user is not watching; report the outcome briefly.) {strip_marker(rem['text'])}",
                conversation=conversation or f"job-{rem['id']}",
                confirmer=unattended,
                on_progress=lambda name: run.update(step=name),
            )
            self.ctx.notify(f"{title}: {result}" if routine else f"Задача „{title}“: {result}")
        except Exception as exc:
            self.ctx.notify(f"„{title}“ се провали: {exc}")
        finally:
            if hub:
                hub.running.pop(run_id, None)


def register(registry: ToolRegistry, ctx) -> None:
    store = ctx.store
    ctx.scheduler = ReminderScheduler(ctx)

    # Tasks -----------------------------------------------------------------
    @registry.tool(
        "Add a task to the to-do list.",
        obj({
            "title": ("string", "What needs doing"),
            "due?": ("string", "Due time, ISO 8601 local, e.g. 2026-10-08T18:00"),
            "priority?": ("string", "low | normal | high"),
            "notes?": ("string", "Details"),
        }),
    )
    def add_task(title: str, due: str | None = None, priority: str = "normal", notes: str | None = None):
        due_iso = parse_time(due).isoformat() if due else None
        tid = store.insert("tasks", title=title, due=due_iso, priority=priority, notes=notes)
        return f"Task {tid} added."

    @registry.tool(
        "List tasks. By default only open ones, ordered by due date.",
        obj({"include_done?": ("boolean", "Also list completed tasks")}),
    )
    def list_tasks(include_done: bool = False):
        where = "" if include_done else "WHERE done=0"
        return store.query(
            f"SELECT id, title, due, priority, notes, done FROM tasks {where} "
            "ORDER BY done, due IS NULL, due, CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END"
        )

    @registry.tool("Mark a task as done.", obj({"task_id": ("integer", "Task id")}))
    def complete_task(task_id: int):
        n = store.execute("UPDATE tasks SET done=1 WHERE id=?", (task_id,)).rowcount
        return "Done." if n else "No such task."

    @registry.tool(
        "Edit a task.",
        obj({
            "task_id": ("integer", "Task id"),
            "title?": ("string", "New title"),
            "due?": ("string", "New due time, ISO 8601"),
            "priority?": ("string", "low | normal | high"),
            "notes?": ("string", "New notes"),
        }),
    )
    def update_task(task_id: int, title=None, due=None, priority=None, notes=None):
        rows = store.query("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            return "No such task."
        r = rows[0]
        store.execute(
            "UPDATE tasks SET title=?, due=?, priority=?, notes=? WHERE id=?",
            (title or r["title"], parse_time(due).isoformat() if due else r["due"], priority or r["priority"], notes or r["notes"], task_id),
        )
        return "Updated."

    @registry.tool("Delete a task.", obj({"task_id": ("integer", "Task id")}))
    def delete_task(task_id: int):
        n = store.execute("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount
        return "Deleted." if n else "No such task."

    # Reminders -------------------------------------------------------------
    @registry.tool(
        "Set a reminder. It fires at the given time on this computer (spoken/printed), and optionally "
        "as an SMS or a phone call to the owner's phone, or a Telegram message.",
        obj({
            "text": ("string", "What to remind about"),
            "at": ("string", "When, ISO 8601 local time, e.g. 2026-10-08T09:30"),
            "repeat?": ("string", "hourly | daily | weekdays | weekly | monthly | yearly"),
            "channels?": ("array", "Any of: local, sms, call, telegram. Default: local"),
        }),
    )
    def add_reminder(text: str, at: str, repeat: str | None = None, channels: list | None = None):
        when = parse_time(at)
        if repeat and repeat not in REPEATS:
            raise ValueError(f"repeat must be one of {sorted(REPEATS)}")
        chans = channels or ["local"]
        bad = set(chans) - CHANNELS
        if bad:
            raise ValueError(f"Unknown channels {bad}; use {sorted(CHANNELS)}")
        rid = store.insert("reminders", text=text, at=when.isoformat(), repeat=repeat, channels=",".join(chans))
        return f"Reminder {rid} set for {when:%Y-%m-%d %H:%M}" + (f", repeating {repeat}." if repeat else ".")

    @registry.tool(
        "Schedule a job for yourself: at the given time (optionally repeating) you will carry out the "
        "instruction on your own and report the result, e.g. 'summarize my unread e-mail', "
        "'check the price of X and tell me if it is below 500'. Actions that need approval are not taken "
        "unattended; the user is told instead.",
        obj({
            "instruction": ("string", "What to do, self-contained"),
            "at": ("string", "First run, ISO 8601 local time"),
            "repeat?": ("string", "hourly | daily | weekdays | weekly | monthly | yearly"),
        }),
    )
    def schedule_job(instruction: str, at: str, repeat: str | None = None):
        when = parse_time(at)
        if repeat and repeat not in REPEATS:
            raise ValueError(f"repeat must be one of {sorted(REPEATS)}")
        rid = store.insert("reminders", text=instruction, at=when.isoformat(), repeat=repeat, channels=JOB)
        return f"Job {rid} scheduled for {when:%Y-%m-%d %H:%M}" + (f", repeating {repeat}." if repeat else ".")

    @registry.tool("List upcoming reminders and scheduled jobs (channel 'agent').", obj({}))
    def list_reminders():
        return store.query("SELECT id, text, at, repeat, channels FROM reminders WHERE fired=0 ORDER BY at")

    @registry.tool("Cancel a reminder or scheduled job.", obj({"reminder_id": ("integer", "Reminder or job id")}))
    def cancel_reminder(reminder_id: int):
        n = store.execute("DELETE FROM reminders WHERE id=?", (reminder_id,)).rowcount
        return "Cancelled." if n else "No such reminder."

    # Calendar --------------------------------------------------------------
    @registry.tool(
        "Add a calendar event.",
        obj({
            "title": ("string", "Event title"),
            "start": ("string", "Start, ISO 8601 local"),
            "end?": ("string", "End, ISO 8601 local"),
            "location?": ("string", "Where"),
            "notes?": ("string", "Details"),
        }),
    )
    def add_event(title: str, start: str, end: str | None = None, location: str | None = None, notes: str | None = None):
        s = parse_time(start)
        e = parse_time(end) if end else s + timedelta(hours=1)
        eid = store.insert("events", title=title, start=s.isoformat(), end=e.isoformat(), location=location, notes=notes)
        return f"Event {eid} added: {title} {s:%Y-%m-%d %H:%M}."

    @registry.tool(
        "List calendar events between two times (default: the next 7 days).",
        obj({"start?": ("string", "From, ISO 8601"), "end?": ("string", "To, ISO 8601")}),
    )
    def list_events(start: str | None = None, end: str | None = None):
        s = parse_time(start) if start else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        e = parse_time(end) if end else s + timedelta(days=7)
        return store.query(
            "SELECT id, title, start, end, location, notes FROM events WHERE start<? AND end>=? ORDER BY start",
            (e.isoformat(), s.isoformat()),
        )

    @registry.tool("Delete a calendar event.", obj({"event_id": ("integer", "Event id")}))
    def delete_event(event_id: int):
        n = store.execute("DELETE FROM events WHERE id=?", (event_id,)).rowcount
        return "Deleted." if n else "No such event."

    @registry.tool(
        "Export the calendar to an .ics file (importable into Google Calendar, Outlook, Apple Calendar).",
        obj({"path?": ("string", "Where to write; default ~/.jarvis/calendar.ics")}),
    )
    def export_calendar(path: str | None = None):
        target = Path(path).expanduser() if path else ctx.settings.home / "calendar.ics"
        target.write_text(to_ics(store.query("SELECT * FROM events ORDER BY start")), encoding="utf-8")
        return f"Exported to {target}."

    @registry.tool("Import events from an .ics file.", obj({"path": ("string", "Path to the .ics file")}))
    def import_calendar(path: str):
        events = from_ics(Path(path).expanduser().read_text(encoding="utf-8"))
        for ev in events:
            store.insert("events", **ev)
        return f"Imported {len(events)} events."


def _ics_time(value: str) -> str:
    return parse_time(value).strftime("%Y%m%dT%H%M%S")


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def to_ics(events: list[dict]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jarvis//EN"]
    for ev in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:jarvis-{ev['id']}@local",
            f"DTSTART:{_ics_time(ev['start'])}",
            f"DTEND:{_ics_time(ev['end'] or ev['start'])}",
            f"SUMMARY:{_ics_escape(ev['title'])}",
        ]
        if ev.get("location"):
            lines.append(f"LOCATION:{_ics_escape(ev['location'])}")
        if ev.get("notes"):
            lines.append(f"DESCRIPTION:{_ics_escape(ev['notes'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def from_ics(text: str) -> list[dict]:
    unfolded = text.replace("\r\n ", "").replace("\n ", "")
    events, current = [], None
    for raw in unfolded.splitlines():
        line = raw.strip()
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            if "title" in current and "start" in current:
                current.setdefault("end", current["start"])
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            key = key.split(";")[0]
            value = value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
            if key in ("DTSTART", "DTEND"):
                fmt = "%Y%m%dT%H%M%S" if "T" in value else "%Y%m%d"
                dt = datetime.strptime(value.rstrip("Z"), fmt)
                current["start" if key == "DTSTART" else "end"] = dt.isoformat()
            elif key == "SUMMARY":
                current["title"] = value
            elif key == "LOCATION":
                current["location"] = value
            elif key == "DESCRIPTION":
                current["notes"] = value
    return events
