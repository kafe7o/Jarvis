"""Reaching the owner when they are away: one question with the options, then the task waits for the answer."""

from __future__ import annotations

import os

from conftest import Approver, FakeClient, response, text_block, tool_block

from jarvis import notices
from jarvis.brain import TURN, Jarvis


def test_the_question_reaches_every_channel_and_is_asked_once(ctx, registry, monkeypatch):
    TURN.set(None)
    told = []
    ctx.notifiers.append(told.append)
    out, err = registry.run("ask_owner", {"question": "Кой от тримата да резервирам?", "options": ["Иван", "Петър", "всички"],
                                          "task": "резервация за четвъртък"}, Approver())
    assert not err and "stop this task now" in out and "Telegram/push" in out
    assert told == ["❓ Кой от тримата да резервирам?\n1. Иван\n2. Петър\n3. всички\n(задача: резервация за четвъртък)"]
    rows = notices.unseen(ctx.store)
    assert len(rows) == 1 and rows[0]["kind"] == "question"
    out, err = registry.run("ask_owner", {"question": "А да пратя ли мейл?"}, Approver())
    assert not err and out.startswith("Not sent: you already asked") and len(told) == 1  # never twice in a row
    out, err = registry.run("ask_owner", {"question": "   "}, Approver())
    assert out == "No question given."


def test_ring_sends_an_urgent_push_and_without_channels_the_app_holds_it(ctx, registry, monkeypatch):
    TURN.set(None)
    sent = []
    monkeypatch.setenv("NTFY_TOPIC", "jarvis-test")

    class Resp:
        def close(self):
            pass

    def urlopen(req, timeout=0):
        sent.append((req.full_url, dict(req.header_items()), req.data.decode()))
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    out, err = registry.run("ask_owner", {"question": "Да платя ли тока?", "ring": True}, Approver())
    assert not err and "urgent push" in out and "the app" in out
    assert sent[0][0] == "https://ntfy.sh/jarvis-test" and sent[0][1].get("Priority") == "5" and "Да платя ли тока?" in sent[0][2]


def test_the_owners_next_message_is_read_as_the_answer_for_that_task(settings, ctx, registry):
    TURN.set(None)
    registry.run("ask_owner", {"question": "Коя дата?", "options": ["четвъртък", "петък"], "task": "среща с Иван"}, Approver())
    client = FakeClient([response(text_block("Записах срещата с Иван за петък."))])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    jarvis.ask("петък", conversation="telegram")
    sent = client.requests[0]["messages"][-1]["content"]
    assert "Коя дата?" in sent and "среща с Иван" in sent and "decision for that task only" in sent
    assert notices.unseen(ctx.store) == []  # answered: not asked again
    assert ctx.store.history("telegram", 5)[0]["content"] == "петък"  # the note is not saved as the owner's words


def test_a_job_that_asks_the_owner_stops_and_the_question_is_kept(settings, ctx, registry):
    TURN.set(None)
    told = []
    ctx.notifiers.append(told.append)
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=FakeClient([
        response(tool_block("ask_owner", {"question": "Да откажа ли абонамента?", "task": "пощата"}), stop="tool_use"),
        response(text_block("Чакам отговор за абонамента."), stop="end_turn"),
    ]))
    registry.run("schedule_job", {"instruction": "Провери пощата", "at": "2026-01-01T08:00"}, Approver())
    job = ctx.store.query("SELECT * FROM reminders ORDER BY id")[0]
    ctx.scheduler.run_job(job)
    kinds = [r["kind"] for r in notices.unseen(ctx.store)]
    assert kinds == ["question", "job"]
    assert any(t.startswith("❓ Да откажа ли абонамента?") for t in told)
    assert "ask_owner once" in ctx.jarvis.local.requests[0]["messages"][0]["content"]


def test_a_guest_cannot_page_the_owner(settings, ctx, registry):
    from jarvis.accounts import Accounts

    accounts = Accounts(ctx.store)
    accounts.set_owner("boss@example.com", "secret1", "Шефа")
    guest = accounts.create("guest@example.com", "Гост", "secret2")
    TURN.set({"user": guest})
    try:
        out, err = registry.run("ask_owner", {"question": "Къде е шефът?"}, Approver())
    finally:
        TURN.set(None)
    assert err and "Only the owner" in out
    assert not os.environ.get("NTFY_TOPIC")
