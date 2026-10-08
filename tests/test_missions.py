"""„Работи до 6:30“ (plugins/missions.py): Jarvis really works on his own in rounds and leaves a report."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

import pytest
from conftest import Approver, FakeClient, response, text_block, tool_block

from jarvis.brain import Jarvis
from jarvis.plugins import missions


def start(registry, goal="Проучи евтини 3D принтери", until=None):
    until = until or (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    out, is_error = registry.run("work_until", {"goal": goal, "until": until, "every_minutes": 15}, Approver())
    assert not is_error, out
    return out


def rounds(store):
    return store.query("SELECT * FROM reminders WHERE fired=0 AND text LIKE '[mission:%' ORDER BY at")


def test_until_is_the_next_time_it_is_that_hour():
    now = datetime(2026, 10, 8, 23, 10)
    assert missions.parse_until("06:30", now) == datetime(2026, 10, 9, 6, 30)
    assert missions.parse_until("23.40", now) == datetime(2026, 10, 8, 23, 40)
    assert missions.parse_until("2026-10-09T07:00", now) == datetime(2026, 10, 9, 7, 0)


def test_work_until_starts_the_first_round_now(ctx, registry):
    out = start(registry)
    assert "Mission 1 started" in out and "every 15 minutes" in out
    mission = missions.get(ctx.store, 1)
    assert mission["state"] == "active" and mission["every"] == 15
    due = rounds(ctx.store)
    assert len(due) == 1 and due[0]["channels"] == "agent" and due[0]["text"].startswith("[mission:1] Проучи")
    assert datetime.fromisoformat(due[0]["at"]) <= datetime.now()
    assert missions.report_path(ctx, mission).exists()
    assert "Проучи евтини 3D принтери: до" in registry.run("work_status", {}, Approver())[0]

    for until in ["2020-01-01T00:00", (datetime.now() + timedelta(minutes=2)).isoformat(timespec="minutes")]:
        assert registry.run("work_until", {"goal": "x", "until": until}, Approver())[1]


def test_the_scheduler_hands_a_round_to_the_mission(ctx, registry, monkeypatch):
    ran = threading.Event()
    seen = []
    monkeypatch.setattr(missions, "run_round", lambda c, mission_id: (seen.append(mission_id), ran.set()))
    start(registry)
    ctx.scheduler.tick()
    assert ran.wait(5) and seen == [1]


def test_a_round_works_with_tools_and_keeps_notes(settings, ctx, registry):
    start(registry)
    client = FakeClient([
        response(tool_block("send_sms", {"to": "+359888111111", "body": "Здрасти"}), stop="tool_use"),
        response(text_block("Мислих.\nROUND: Сравних 5 принтера.\nНай-евтин е A1 mini.")),
    ])
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    ctx.store.execute("UPDATE reminders SET fired=1")  # as the scheduler does when the round is due
    missions.run_round(ctx, 1)
    assert "Round 1" in client.requests[0]["messages"][-1]["content"]
    mission = missions.get(ctx.store, 1)
    assert mission["rounds"] == 1 and "Сравних 5 принтера" in mission["notes"] and "Мислих" not in mission["notes"]
    waiting = json.loads(mission["waiting"])
    assert len(waiting) == 1 and "+359888111111" in waiting[0]  # needed the owner's yes: not sent, kept for the morning
    nxt = rounds(ctx.store)
    assert len(nxt) == 1 and 14 <= (datetime.fromisoformat(nxt[-1]["at"]) - datetime.now()).total_seconds() / 60 <= 15
    assert "Сравних 5 принтера" in missions.report_path(ctx, mission).read_text(encoding="utf-8")


def test_at_the_end_time_he_writes_the_report_and_tells_the_owner(settings, ctx, registry):
    told = []
    ctx.notifiers.append(told.append)
    start(registry, until=(datetime.now() + timedelta(minutes=30)).strftime("%H:%M"))
    ctx.store.execute("UPDATE missions SET rounds=3, notes='### 23:30, кръг 1\nНамерих 5 принтера.', waiting=?",
                      (json.dumps(["SMS до Иван"], ensure_ascii=False),))
    client = FakeClient([response(text_block("Намерих 5 принтера; най-добрият е A1 mini."))])
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    missions.run_round(ctx, 1, now=datetime.now() + timedelta(hours=1))
    mission = missions.get(ctx.store, 1)
    assert mission["state"] == "done" and rounds(ctx.store) == []
    report = missions.report_path(ctx, mission).read_text(encoding="utf-8")
    assert "## Доклад" in report and "A1 mini" in report and "SMS до Иван" in report and "Намерих 5 принтера." in report
    assert told and "Докладът" in told[-1] and "A1 mini" in told[-1]
    assert "Don't use tools" in client.requests[0]["messages"][-1]["content"]
    missions.run_round(ctx, 1)  # a late round does nothing
    assert len(client.requests) == 1


def test_stop_work_ends_it_now_with_a_report(settings, ctx, registry):
    start(registry)
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=FakeClient([response(text_block("Спрях рано."))]))
    out, is_error = registry.run("stop_work", {}, Approver())
    assert not is_error and out.startswith("Спирам")
    deadline = time.time() + 5
    while missions.get(ctx.store, 1)["state"] != "stopped" and time.time() < deadline:
        time.sleep(0.05)
    assert missions.get(ctx.store, 1)["state"] == "stopped" and rounds(ctx.store) == []
    assert registry.run("work_status", {}, Approver())[0] == "Не работя по нищо сам в момента."


def test_only_the_owner_leaves_him_working(ctx, registry):
    from jarvis.brain import TURN

    member = type("Member", (), {"is_owner": False})()
    TURN.set({"user": member})
    try:
        with pytest.raises(PermissionError):
            registry.tools["work_until"].func(goal="x", until="06:30")
    finally:
        TURN.set({})
