from __future__ import annotations

from types import SimpleNamespace

from conftest import Approver

from jarvis import phone_agent
from jarvis.plugins import comms


class FakeTwilio:
    def __init__(self):
        self.calls_made, self.sms = [], []
        self.calls = SimpleNamespace(create=self._call)
        self.messages = SimpleNamespace(create=self._sms)

    def _call(self, **kw):
        self.calls_made.append(kw)
        return SimpleNamespace(sid="CA1")

    def _sms(self, **kw):
        self.sms.append(kw)
        return SimpleNamespace(sid="SM1")


def test_call_uses_contact_and_asks_first(registry, monkeypatch):
    fake = FakeTwilio()
    monkeypatch.setattr(comms, "twilio_client", lambda s: fake)
    registry.run("save_contact", {"name": "Мама", "phone": "+359888222333"}, Approver())

    approver = Approver(True)
    out, err = registry.run("make_call", {"to": "мама", "message": "Ще закъснея & идвам"}, approver)
    assert not err, out
    assert "+359888222333" in approver.asked[0]
    call = fake.calls_made[0]
    assert call["to"] == "+359888222333"
    assert "Ще закъснея &amp; идвам" in call["twiml"]

    out, err = registry.run("send_sms", {"to": "Мама", "body": "Здрасти"}, Approver(False))
    assert err and fake.sms == []


def test_sms_reminder_goes_to_owner(ctx, registry, monkeypatch):
    fake = FakeTwilio()
    monkeypatch.setattr(comms, "twilio_client", lambda s: fake)
    registry.run("add_reminder", {"text": "Среща", "at": "2026-01-01T08:00", "channels": ["sms"]}, Approver())
    from datetime import datetime

    ctx.scheduler.tick(now=datetime(2026, 1, 1, 8, 1))
    assert fake.sms[0]["to"] == "+359888000000" and "Среща" in fake.sms[0]["body"]


class ScriptedBrain:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def next(self, sess):
        self.seen.append(list(sess.transcript))
        return self.replies.pop(0)


def test_phone_conversation_flow(ctx, monkeypatch):
    monkeypatch.setenv("JARVIS_PUBLIC_URL", "https://example.test")
    reports = []
    ctx.notifiers.append(reports.append)
    phone_agent.SESSIONS["tok"] = phone_agent.CallSession(goal="Резервирай маса за двама", language="bg-BG", call_sid="CA9")
    brain = ScriptedBrain([
        {"say": "Здравейте, искам да резервирам маса.", "done": False, "summary": ""},
        {"say": "Благодаря, довиждане!", "done": True, "summary": "Масата е запазена за 20:00."},
    ])
    first = phone_agent.handle(ctx, brain, "/voice/agent/tok", {})
    assert "<Gather" in first and "https://example.test/voice/agent/tok" in first
    last = phone_agent.handle(ctx, brain, "/voice/agent/tok", {"SpeechResult": "Да, за 20 часа е свободно."})
    assert "<Hangup/>" in last
    assert brain.seen[1][-1] == {"who": "them", "text": "Да, за 20 часа е свободно."}
    phone_agent.handle(ctx, brain, "/voice/status/tok", {"CallStatus": "completed"})
    assert reports and "Масата е запазена" in reports[0]
    assert phone_agent.transcript_for("CA9")[-1] == {"summary": "Масата е запазена за 20:00."}


def test_phone_brain_merges_turns_for_the_api(ctx):
    from conftest import FakeClient, response, text_block

    client = FakeClient([response(text_block('{"say": "Ало?", "done": false, "summary": ""}'))])
    brain = phone_agent.PhoneBrain(ctx, client=client)
    sess = phone_agent.CallSession(goal="", language="bg-BG", inbound=True)
    sess.transcript = [{"who": "system", "text": "Caller number: +1"}, {"who": "them", "text": "Ало"}]
    assert brain.next(sess)["say"] == "Ало?"
    roles = [m["role"] for m in client.requests[0]["messages"]]
    assert roles == ["user"]
