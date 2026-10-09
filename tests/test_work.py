from __future__ import annotations

import json

from conftest import Approver, FakeClient, response, text_block, tool_block
from test_hub import call, make_hub
from test_team import TeamClient

from jarvis.accounts import User
from jarvis.brain import Jarvis
from jarvis.work import MAX_STEPS, Board

OWNER = User(1, "owner@example.com", "Анастас", "owner")


def test_the_board_follows_a_piece_of_work_from_start_to_finish():
    seen = []
    board = Board(label={"web_search": "Търси в интернет"}.get, publish=lambda run, to: seen.append((run, to)))
    run_id, _ = board.start("Анастас", "Намери полет до Лондон")
    report = board.reporter(run_id)
    report({"type": "level", "level": 3})
    report({"type": "step", "id": 1, "agent": "Jarvis", "tool": "web_search", "detail": "полети Лондон", "state": "running"})
    report({"type": "step", "id": 1, "state": "waiting"})
    now = board.snapshot()["running"][0]
    assert now["waiting"] and now["step"] == "Чака твоето „да“" and now["steps"][0]["ended"] is None
    report({"type": "step", "id": 1, "state": "running"})
    assert board.snapshot()["running"][0]["step"] == "Търси в интернет"
    report({"type": "step", "id": 1, "state": "ok"})
    report({"type": "agent", "agent": "Изследователят", "task": "сравни цените", "state": "running"})
    report({"type": "agent", "agent": "Изследователят", "state": "done"})
    report({"type": "plan", "plan": {"id": 4, "goal": "Полет", "status": "active", "steps": []}})
    board.finish(run_id, ok=True)

    snap = board.snapshot()
    assert snap["running"] == [] and len(snap["recent"]) == 1
    done = snap["recent"][0]
    assert done["state"] == "done" and done["level"] == 3 and done["plan"]["id"] == 4
    assert done["steps"][0]["state"] == "ok" and done["steps"][0]["label"] == "Търси в интернет"
    assert done["agents"][0]["state"] == "done" and done["agents"][0]["ended"]
    assert len(seen) == 10 and seen[-1][0]["state"] == "done"
    report({"type": "level", "level": 1})  # a late event for finished work changes nothing
    assert board.snapshot()["recent"][0]["level"] == 3


def test_the_board_stays_small():
    board = Board(keep=2)
    for n in range(3):
        run_id, _ = board.start("Анастас", f"задача {n}")
        report = board.reporter(run_id)
        for i in range(MAX_STEPS + 5):
            report({"type": "step", "id": i, "tool": "recall", "state": "running"})
        board.finish(run_id, ok=False)
    recent = board.snapshot()["recent"]
    assert [r["text"] for r in recent] == ["задача 2", "задача 1"]
    assert len(recent[0]["steps"]) == MAX_STEPS and all(s["state"] == "error" for s in recent[0]["steps"])


def test_jarvis_reports_each_step_its_plan_and_a_declined_confirmation(settings, ctx, registry):
    client = FakeClient([
        response(tool_block("make_plan", {"goal": "Ремонт на банята", "steps": ["1 Огледай", "2 Купи плочки"]}),
                 stop="tool_use"),
        response(tool_block("update_plan_step", {"plan_id": 1, "step_id": "1", "status": "done"}, id="tu_2"),
                 stop="tool_use"),
        response(tool_block("request_approval", {"action": "Поръчай плочки за 300 лв."}, id="tu_3"), stop="tool_use"),
        response(text_block("Планът е готов.")),
    ])
    events = []
    jarvis = Jarvis(settings, ctx.store, registry, Approver(False), client=client)
    assert jarvis.ask("Направи план за банята", user=OWNER, on_step=events.append) == "Планът е готов."

    assert events[0] == {"type": "level", "level": 3}
    steps = [e for e in events if e["type"] == "step"]
    started = [e for e in steps if e.get("tool")]
    assert [e["tool"] for e in started] == ["make_plan", "update_plan_step", "request_approval"]
    assert started[2]["detail"] == "Поръчай плочки за 300 лв." and started[0]["agent"] == "Jarvis"
    approval = [e["state"] for e in steps if e["id"] == started[2]["id"]]
    assert approval == ["running", "waiting", "running", "declined"]
    plans = [e["plan"] for e in events if e["type"] == "plan"]
    assert [p["steps"][0]["status"] for p in plans] == ["todo", "done"] and plans[0]["goal"] == "Ремонт на банята"


def test_specialists_show_up_on_the_board(settings, ctx, registry):
    client = TeamClient({
        "Jarvis": [
            response(tool_block("delegate", {"assignments": [{"agent": "researcher", "task": "Намери 3 идеи"}]}),
                     stop="tool_use"),
            response(text_block("Готово, сър.")),
        ],
        "Изследователят": [
            response(tool_block("recall", {"query": "идеи"}, id="tu_7"), stop="tool_use"),
            response(text_block("Три идеи.")),
        ],
    })
    board = Board()
    run_id, _ = board.start("Анастас", "Идеи за видео")
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    ctx.jarvis.ask("Идеи за видео", user=OWNER, on_step=board.reporter(run_id))
    run = board.snapshot()["running"][0]
    assert run["agents"] == [{**run["agents"][0], "name": "Изследователят", "task": "Намери 3 идеи", "state": "done"}]
    assert [(s["agent"], s["tool"], s["state"]) for s in run["steps"]] == [
        ("Jarvis", "delegate", "ok"), ("Изследователят", "recall", "ok")]


def test_the_app_sees_work_live_and_a_member_only_their_own(settings, ctx, registry):
    hub, port = make_hub(settings, ctx, registry, [
        response(tool_block("recall", {"query": "Лондон"}), stop="tool_use"),
        response(text_block("Нищо не помня за Лондон.")),
    ])
    owner = hub.accounts.create("owner@example.com", "Анастас", "123456", role="owner")
    member = hub.accounts.create("m@example.com", "Мария", "123456")
    hub.ask("Какво знаеш за Лондон?", user=owner)
    work = json.loads(call(port, "/api/work")[1])
    assert work["running"] == [] and work["recent"][0]["text"] == "Какво знаеш за Лондон?"
    assert work["recent"][0]["steps"][0]["label"] == "Спомня си" and work["recent"][0]["steps"][0]["detail"] == "Лондон"
    live = [e for e in hub.events.events if e["kind"] == "work"]
    assert live and all(e["to"] is None for e in live) and live[-1]["run"]["state"] == "done"

    run_id, _ = hub.track(member.name, "моята задача", to=member.id)
    assert [r["text"] for r in hub.work_for(member)["running"]] == ["моята задача"]
    assert hub.work_for(member)["recent"] == []
    assert len(hub.work_for(owner)["running"]) == 1
    hub.board.finish(run_id)
    assert {e["to"] for e in hub.events.events if e["kind"] == "work"} == {None, member.id}
