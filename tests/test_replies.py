from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import Approver, FakeClient, response, system_text, text_block

from jarvis.accounts import User
from jarvis.brain import Jarvis
from jarvis.plugins import replies

OWNER = User(1, "owner@example.com", "Анастас", "owner")


def record(pkg, title, text, group=False):
    return (f"  NotificationRecord(0x0a1b2c: pkg={pkg} user=UserHandle{{0}} id=1 tag=null importance=4 "
            f"key=0|{pkg}|1|null|10234: Notification(channel=msg)\n"
            "      extras={\n"
            f"        android.title=String ({title})\n"
            f"        android.text=String ({text})\n"
            f"        android.isGroupConversation=Boolean ({'true' if group else 'false'})\n"
            "      }\n")


DUMP = "Current Notification Manager state:\n  Notification List:\n" + "".join([
    record("com.whatsapp", "Мария", "Ще дойдеш ли довечера? :)"),
    record("com.whatsapp", "Семейство", "Петър: кой взима хляб?", group=True),
    record("com.whatsapp", "WhatsApp", "5 нови съобщения"),
    record("com.instagram.android", "ivan.petrov", "Видя ли поста ми?"),
    record("com.google.android.gm", "Банка", "Вашето извлечение"),
    "  NotificationRecord(0x0d: pkg=com.viber.voip user=UserHandle{0}\n        android.title=String [length=4]\n",
])


def test_only_messages_from_people_count():
    found = replies.messages_in(DUMP)
    assert [(m["app"], m["sender"], m["text"]) for m in found] == [
        ("WhatsApp", "Мария", "Ще дойдеш ли довечера? :)"), ("Instagram", "ivan.petrov", "Видя ли поста ми?")]


class FakePhone:
    """The phone's notifications and screens: each look at the screen shows the next one."""

    def __init__(self, dump, screens=()):
        self.dump, self.screens, self.commands, self.typed = dump, list(screens), [], []

    def devices(self):
        return {"phone": "USB123"}

    lock = "mDreamingLockscreen=false mShowingLockscreen=false"

    def shell(self, device, command, timeout=30):
        self.commands.append(command)
        if command.startswith("dumpsys window"):
            return self.lock
        return self.dump if command.startswith("dumpsys notification") else ""

    def elements(self, device):
        return self.screens.pop(0)

    def type_text(self, device, text):
        self.typed.append(text)
        return "Typed."


def item(label, x, y, field=False, tap=True):
    return {"label": label, "x": x, "y": y, "kind": "EditText" if field else "View", "tap": tap, "field": field,
            "checked": None}


SHADE = [item("Мария", 300, 400), item("Ще дойдеш ли довечера? :)", 300, 450)]
CHAT = [item("Мария", 200, 100), item("Съобщение", 400, 2100, field=True)]
TYPED = CHAT + [item("Изпрати", 1000, 2100)]


@pytest.fixture
def setup(settings, ctx, registry, monkeypatch):
    monkeypatch.setattr(replies, "save_settings", lambda _ctx, values: [monkeypatch.setenv(k, v) for k, v in values.items()])
    monkeypatch.setattr(replies, "pause", lambda s: None)
    monkeypatch.setattr(replies.threading, "Thread", lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)))
    settings.quiet_hours = ""

    def make(drafts, answer=True, dump=DUMP, screens=()):
        client = FakeClient([response(text_block(d)) for d in drafts])
        ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
        ctx.android = FakePhone(dump, screens)
        ctx.hub = SimpleNamespace(confirmer=Approver(answer))
        return client, ctx.android, ctx.hub.confirmer
    return make


def test_a_new_message_gets_a_reply_sent_only_after_yes(setup, ctx, monkeypatch):
    client, phone, approver = setup(["Ще ти пиша малко по-късно."], screens=[SHADE, CHAT, TYPED])
    monkeypatch.setenv("JARVIS_REPLY_STYLE", "кратко, на ти")
    watcher = ctx.replies
    assert watcher.tick() == 0 and phone.commands == []  # off: the phone is not even looked at
    out, _ = ctx.jarvis.registry.run("auto_reply", {"action": "on"}, Approver())
    assert "„да“" in out and replies.enabled()

    phone.dump = DUMP.replace(record("com.whatsapp", "Мария", "Ще дойдеш ли довечера? :)"), "")
    assert watcher.tick() == 0  # the first look only notes what was already there
    assert {r["sender"] for r in ctx.store.query("SELECT sender FROM inbox WHERE state='old'")} == {"ivan.petrov"}

    phone.dump = DUMP  # Мария writes
    assert watcher.tick() == 1
    draft = client.requests[0]
    assert draft["model"] == "claude-haiku-5-5" and not draft.get("tools")
    assert "кратко, на ти" in system_text(draft) and "Мария" in draft["messages"][0]["content"]
    assert approver.asked == ["ОТГОВОР в WhatsApp до Мария\nПиса: „Ще дойдеш ли довечера? :)“\n"
                              "Отговор: „Ще ти пиша малко по-късно.“"]
    assert phone.typed == ["Ще ти пиша малко по-късно."]
    assert "input tap 300 450" in phone.commands and "input tap 400 2100" in phone.commands
    assert "input tap 1000 2100" in phone.commands  # Send
    row = ctx.store.query("SELECT state, reply FROM inbox WHERE sender='Мария'")[0]
    assert row == {"state": "sent", "reply": "Ще ти пиша малко по-късно."}
    assert watcher.tick() == 0  # nothing new, no AI
    assert len(client.requests) == 1


def test_no_yes_means_nothing_is_sent(setup, ctx, monkeypatch):
    _client, phone, approver = setup(["Да, идвам!", "Да, супер е!"], answer=False)
    monkeypatch.setenv("JARVIS_AUTO_REPLY", "1")
    ctx.replies.ready = True
    assert ctx.replies.tick() == 2 and len(approver.asked) == 2
    assert phone.typed == [] and not any(c.startswith("input tap") for c in phone.commands)
    assert [r["state"] for r in ctx.store.query("SELECT state FROM inbox")] == ["declined", "declined"]


def test_ads_and_codes_get_no_reply(setup, ctx):
    _client, _phone, approver = setup(["SKIP", "SKIP"])
    assert ctx.jarvis.registry.run("auto_reply", {"action": "check"}, Approver())[0].startswith("Новите съобщения")
    assert approver.asked == [] and [r["state"] for r in ctx.store.query("SELECT state FROM inbox")] == ["skipped"] * 2


def test_a_message_already_read_is_reported_not_guessed(setup, ctx):
    _client, phone, _approver = setup(["Супер!"], screens=[[item("Друго", 10, 10)]])
    notes = []
    ctx.notifiers.append(notes.append)
    ctx.replies.answer(replies.messages_in(record("com.instagram.android", "ivan.petrov", "Видя ли поста ми?")))
    row = ctx.store.query("SELECT state FROM inbox")[0]
    assert row["state"] == "failed" and phone.typed == [] and "вече не е в известията" in notes[0]
    assert "cmd statusbar collapse" in phone.commands


def test_saying_it_switches_it_on_without_ai(settings, ctx, registry, monkeypatch):
    monkeypatch.setattr(replies, "save_settings", lambda _ctx, values: [monkeypatch.setenv(k, v) for k, v in values.items()])
    settings.router = True
    ctx.android = FakePhone("")
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]))
    assert "Нищо не пращам без твоето „да“" in ctx.jarvis.ask("Джарвис, отговаряй вместо мен", user=OWNER)
    assert replies.enabled()
    assert ctx.jarvis.ask("спри да отговаряш", user=OWNER) == "Спрях да отговарям вместо теб."
    assert not replies.enabled()


def test_a_locked_phone_is_waited_for_then_reported(setup, ctx):
    _client, phone, _approver = setup(["Идвам!"])
    phone.lock = "mShowingLockscreen=true"
    notes = []
    ctx.notifiers.append(notes.append)
    ctx.replies.answer(replies.messages_in(record("com.viber.voip", "Иван", "Къде си?")))
    assert sum(c.startswith("dumpsys window") for c in phone.commands) == 12 and phone.typed == []
    assert notes == ["Не успях да пратя отговора до Иван в Viber: телефонът е заключен. Отключи го и отговори сам, "
                     "или ми кажи пак"]
