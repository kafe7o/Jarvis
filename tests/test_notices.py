"""What happened while the owner was away is said first at the next chance, once."""

from __future__ import annotations

from conftest import Approver, FakeClient, response, text_block, tool_block

from jarvis import notices
from jarvis.brain import Jarvis


def test_notices_are_kept_told_once_and_the_note_gives_the_gist(ctx):
    assert notices.add(ctx.store, "   ") is None
    for n in range(10):
        notices.add(ctx.store, f"Задачата „Поща {n}“ мина: 3 писма")
    rows = notices.unseen(ctx.store)
    assert len(rows) == 10
    note = notices.hint(rows)
    assert "Поща 0" in note and "Поща 7" in note and "Поща 8" not in note and "and 2 more" in note
    assert "never mention this note" in note
    notices.seen(ctx.store, rows)
    assert notices.unseen(ctx.store) == [] and notices.hint([]) == ""


def test_a_scheduled_job_leaves_a_notice_of_how_it_went(settings, ctx, registry):
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=FakeClient([
        response(text_block("Прочетох пощата: 2 нови."), stop="end_turn"),
        response(tool_block("send_sms", {"to": "+359888111111", "body": "Здрасти"}), stop="tool_use"),
        response(text_block("Чака одобрение."), stop="end_turn"),
    ]))
    registry.run("schedule_job", {"instruction": "Провери пощата", "at": "2026-01-01T08:00"}, Approver())
    registry.run("schedule_job", {"instruction": "Пиши на Иван", "at": "2026-01-01T09:00"}, Approver())
    for job in ctx.store.query("SELECT * FROM reminders ORDER BY id"):
        ctx.scheduler.run_job(job)
    texts = [r["text"] for r in notices.unseen(ctx.store)]
    assert texts[0] == "Задачата „Провери пощата“ мина: Прочетох пощата: 2 нови."
    assert texts[1].startswith("Задачата „Пиши на Иван“ стигна до стъпка, която иска твоето „да“")
    assert texts[2].startswith("Задачата „Пиши на Иван“ мина:")


def test_the_owners_next_message_carries_the_news_and_then_it_is_not_repeated(settings, ctx, registry):
    notices.add(ctx.store, "Задачата „Нощна поща“ се провали: Gemini не отговори")
    client = FakeClient([response(text_block("Първо: нощната поща не мина. Иначе всичко е наред."))])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    jarvis.ask("какво става", conversation="main")
    sent = client.requests[0]["messages"][-1]["content"]
    assert "Нощна поща" in sent and "a failure or a mistake of yours before good news" in sent
    assert "Нощна поща" not in ctx.store.history("main", 5)[0]["content"]  # the note is not saved as said by the owner
    assert notices.unseen(ctx.store) == []
    client.responses.append(response(text_block("Да.")))
    jarvis.ask("добре", conversation="main")
    assert "Нощна поща" not in client.requests[1]["messages"][-1]["content"]


def test_a_jobs_own_run_and_a_guest_never_get_the_owners_news(settings, ctx, registry):
    from jarvis.accounts import Accounts

    notices.add(ctx.store, "Задачата „Нощна поща“ мина: 2 писма")
    client = FakeClient([response(text_block("Готово.")), response(text_block("Здрасти."))])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    jarvis.ask("(Scheduled job, the user is not watching; report the outcome briefly.) Подреди бележките",
               conversation="job-1", route=False)
    assert "Нощна поща" not in client.requests[0]["messages"][-1]["content"]
    accounts = Accounts(ctx.store)
    accounts.set_owner("boss@example.com", "secret1", "Шефа")
    guest = accounts.create("guest@example.com", "Гост", "secret2")
    from jarvis.brain import TURN

    try:
        jarvis.ask("здрасти, кой си ти?", conversation="guest", user=guest)
    finally:
        TURN.set(None)  # the guest's turn must not linger in this thread for the next test
    assert "Нощна поща" not in client.requests[1]["messages"][-1]["content"]
    assert len(notices.unseen(ctx.store)) == 1


def test_the_quick_lane_tells_the_news_too(settings, ctx, registry):
    settings.router = True
    notices.add(ctx.store, "Задачата „Сутрешен брифинг“ мина: 3 срещи днес")
    client = FakeClient([response(text_block("Сутринта: 3 срещи. Столицата е Париж."))])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    assert jarvis.ask("коя е столицата на Франция?", conversation="main").startswith("Сутринта")
    assert "Сутрешен брифинг" in client.requests[0]["messages"][-1]["content"]
    assert notices.unseen(ctx.store) == []
