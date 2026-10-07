from __future__ import annotations

from datetime import datetime

from conftest import Approver, FakeClient, response, text_block, tool_block

from jarvis.brain import Jarvis
from jarvis.confirm import is_yes
from jarvis.plugins.tasks import from_ics, next_occurrence, to_ics
from jarvis.voice.wake import strip_wake_word


def test_every_tool_has_a_valid_schema(registry):
    for tool in registry.definitions():
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert set(schema["required"]) <= set(schema["properties"])
        assert tool["description"]


def test_people_and_money_tools_always_need_confirmation(registry):
    for name in ["make_call", "connect_call", "agent_call", "send_sms", "send_email",
                 "create_payment_link", "send_invoice", "refund_payment", "payout"]:
        tool = registry.tools[name]
        assert tool.confirm and not tool.local, name


def test_declined_action_does_not_run(registry, ctx, tmp_path):
    approver = Approver(False)
    target = tmp_path / "x.txt"
    out, err = registry.run("write_file", {"path": str(target), "content": "hi"}, approver)
    assert err and "declined" in out
    assert not target.exists()
    assert "x.txt" in approver.asked[0]


def test_trust_local_skips_prompt_only_for_local_tools(registry, tmp_path):
    approver = Approver(False)
    target = tmp_path / "y.txt"
    out, err = registry.run("write_file", {"path": str(target), "content": "hi"}, approver, trust_local=True)
    assert not err and target.read_text() == "hi"
    out, err = registry.run("send_sms", {"to": "+359888111111", "body": "hi"}, approver, trust_local=True)
    assert err and "declined" in out


def test_files_outside_allowed_roots_are_refused(registry):
    out, err = registry.run("read_file", {"path": "/etc/hostname"}, Approver())
    assert err and "outside the allowed folders" in out


def test_unconfigured_service_names_missing_keys(registry):
    out, err = registry.run("send_sms", {"to": "+359888111111", "body": "hi"}, Approver())
    assert err and "TWILIO_ACCOUNT_SID" in out
    out, err = registry.run("payment_balance", {}, Approver())
    assert err and "STRIPE_API_KEY" in out


def test_brain_runs_tools_and_returns_final_answer(settings, ctx, registry):
    client = FakeClient([
        response(text_block("Добавям."), tool_block("add_task", {"title": "Купи мляко", "priority": "high"}), stop="tool_use"),
        response(text_block("Готово, сър. Добавих го."), stop="end_turn"),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("Добави задача да купя мляко") == "Готово, сър. Добавих го."
    assert ctx.store.query("SELECT title, priority FROM tasks") == [{"title": "Купи мляко", "priority": "high"}]

    first = client.requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default"
    names = {t["name"] for t in first["tools"]}
    assert {"web_search", "web_fetch", "make_call", "add_reminder", "run_shell"} <= names
    tool_result = client.requests[1]["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result" and not tool_result["is_error"]

    # The next question carries the previous turn as plain-text history.
    client.responses.append(response(text_block("Мляко."), stop="end_turn"))
    jarvis.ask("Какво добавихме?")
    msgs = client.requests[-1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]


def test_remembered_facts_reach_the_system_prompt(settings, ctx, registry):
    registry.run("remember", {"topic": "храна", "fact": "Обича кафе без захар"}, Approver())
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]))
    assert "Обича кафе без захар" in jarvis.system_prompt()


def test_reminders_fire_and_repeat(ctx, registry):
    heard = []
    ctx.notifiers.append(heard.append)
    registry.run("add_reminder", {"text": "Лекарство", "at": "2026-01-01T08:00", "repeat": "daily"}, Approver())
    registry.run("add_reminder", {"text": "Еднократно", "at": "2026-01-01T09:00"}, Approver())
    fired = ctx.scheduler.tick(now=datetime(2026, 1, 3, 9, 30))
    assert len(fired) == 2 and len(heard) == 2
    pending = ctx.store.query("SELECT text, at FROM reminders WHERE fired=0")
    assert pending == [{"text": "Лекарство", "at": "2026-01-04T08:00:00"}]


def test_next_occurrence_handles_month_ends():
    assert next_occurrence(datetime(2026, 1, 31, 9), "monthly") == datetime(2026, 2, 28, 9)
    assert next_occurrence(datetime(2026, 10, 9, 9), "weekdays") == datetime(2026, 10, 12, 9)


def test_calendar_ics_round_trip(registry, ctx):
    registry.run("add_event", {"title": "Среща, важна", "start": "2026-10-08T10:00", "location": "София"}, Approver())
    events = ctx.store.query("SELECT * FROM events")
    parsed = from_ics(to_ics(events))
    assert parsed[0]["title"] == "Среща, важна"
    assert parsed[0]["start"] == "2026-10-08T10:00:00" and parsed[0]["end"] == "2026-10-08T11:00:00"
    out, _ = registry.run("list_events", {"start": "2026-10-08T00:00", "end": "2026-10-09T00:00"}, Approver())
    assert "Среща" in out


def test_wake_word_and_yes_no():
    assert strip_wake_word("Джарвис, колко е часът?") == "колко е часът"
    assert strip_wake_word("Hey Jarvis") == ""
    assert strip_wake_word("здравей") is None
    assert is_yes("Да, давай") and not is_yes("не, недей") and not is_yes("хмм")
