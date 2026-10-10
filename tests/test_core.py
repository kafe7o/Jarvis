from __future__ import annotations

import json
from datetime import datetime

from conftest import Approver, FakeClient, response, system_text, text_block, tool_block

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
                 "create_payment_link", "send_invoice", "refund_payment", "payout", "request_approval",
                 "whatsapp_send", "viber_send", "gmail_send", "google_calendar_invite", "paypal_send",
                 "bank_transfer", "home_security", "phone_call", "phone_answer", "phone_sms"]:
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


def test_image_results_become_image_blocks(registry):
    from jarvis.tools import Image

    @registry.tool("Test picture.", {"type": "object", "properties": {}, "required": []})
    def picture():
        return Image(b"\x89PNG", "image/png", "a screen")

    out, err = registry.run("picture", {}, Approver())
    assert not err and out[0]["type"] == "image" and out[1] == {"type": "text", "text": "a screen"}


def test_scheduled_job_runs_unattended_and_never_auto_approves(settings, ctx, registry):
    reports = []
    ctx.notifiers.append(reports.append)
    client = FakeClient([
        response(tool_block("send_sms", {"to": "+359888111111", "body": "Здрасти"}), stop="tool_use"),
        response(text_block("Не изпратих SMS-а, чака одобрение."), stop="end_turn"),
    ])
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    registry.run("schedule_job", {"instruction": "Пиши на Иван", "at": "2026-01-01T08:00"}, Approver())
    job = ctx.store.query("SELECT * FROM reminders")[0]
    ctx.scheduler.run_job(job)
    assert any("одобрение" in r for r in reports)
    assert "declined" in client.requests[1]["messages"][2]["content"][0]["content"]
    assert reports[-1].startswith("Задача „Пиши на Иван“")


def test_create_skill_adds_tools_now_and_after_restart(settings, ctx, registry):
    from jarvis.plugins import load_all
    from jarvis.tools import ToolRegistry

    code = (
        "from jarvis.tools import obj\n"
        "def register(registry, ctx):\n"
        "    @registry.tool('Double a number.', obj({'n': ('integer', 'Number')}))\n"
        "    def double(n: int):\n"
        "        return str(n * 2)\n"
    )
    out, err = registry.run("create_skill", {"name": "math_tools", "code": code}, Approver())
    assert not err and "double" in out
    assert registry.run("double", {"n": 21}, Approver()) == ("42", False)

    fresh = ToolRegistry()
    load_all(fresh, ctx)
    assert "double" in fresh.tools

    out, err = registry.run("create_skill", {"name": "broken", "code": "def register(r, c): raise RuntimeError('x')"}, Approver())
    assert "failed to load" in out
    assert not (settings.home / "skills" / "broken.py").exists()


def test_plan_tree_tracks_progress(registry):
    out, err = registry.run("make_plan", {"goal": "Почивка", "steps": ["1 Избери дестинация", "1.1 Сравни цени", "2 Резервирай"]}, Approver())
    assert not err and "○ 1 Избери дестинация" in out and "  ○ 1.1 Сравни цени" in out
    plan_id = int(out.split(":")[0].split()[-1])
    out, _ = registry.run("update_plan_step", {"plan_id": plan_id, "step_id": "1.1", "status": "done", "note": "Гърция е най-евтина"}, Approver())
    assert "✓ 1.1 Сравни цени — Гърция е най-евтина" in out
    for sid in ["1", "2"]:
        out, _ = registry.run("update_plan_step", {"plan_id": plan_id, "step_id": sid, "status": "done"}, Approver())
    assert "[done]" in out


def test_search_history_finds_old_conversations(ctx, registry):
    ctx.store.add_message("telegram", "user", "Паролата за WiFi в офиса е на стикера")
    out, _ = registry.run("search_history", {"query": "wifi"}, Approver())
    assert "стикера" in out


def test_heartbeat_notifies_only_when_useful(settings, ctx, registry):
    from jarvis.plugins.tasks import in_quiet_hours

    reports = []
    ctx.notifiers.append(reports.append)
    client = FakeClient([
        response(text_block("NOTHING"), stop="end_turn"),
        response(text_block("NOTIFY: Срещата ти е след 30 минути."), stop="end_turn"),
    ])
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    assert ctx.scheduler.heartbeat() is None and reports == []
    assert ctx.scheduler.heartbeat() == "Срещата ти е след 30 минути." and reports == ["Срещата ти е след 30 минути."]
    assert in_quiet_hours("23-7", datetime(2026, 1, 1, 2)) and not in_quiet_hours("23-7", datetime(2026, 1, 1, 12))


def test_system_prompt_is_fixed_for_a_turn_and_bad_thinking_is_dropped(settings, ctx, registry, monkeypatch):
    import anthropic

    from conftest import FakeClient, response, text_block, tool_block
    from jarvis.brain import Jarvis
    from types import SimpleNamespace

    thinking = SimpleNamespace(type="thinking", thinking="...", signature="sig")
    client = FakeClient([
        response(thinking, tool_block("remember", {"topic": "t", "fact": "нов факт"}), stop="tool_use"),
        response(text_block("Запомних.")),
    ])
    calls = {"n": 0}
    original = client._create

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 2 and any(getattr(b, "type", None) == "thinking" for m in kwargs["messages"]
                                   if m["role"] == "assistant" and not isinstance(m["content"], str) for b in m["content"]):
            err = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
            Exception.__init__(err, "Invalid `signature` in `thinking` block")
            raise err
        return original(**kwargs)

    client.beta.messages.create = create
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=client)
    assert jarvis.ask("запомни") == "Запомних."
    first, second = client.requests
    assert first["system"] == second["system"]  # the new fact did not change the prompt mid-turn
    assert all(getattr(b, "type", None) != "thinking" for b in second["messages"][-2]["content"])


def test_phone_is_found_over_usb_without_settings(monkeypatch):
    from jarvis.plugins import android

    monkeypatch.delenv("JARVIS_ADB_DEVICES", raising=False)
    monkeypatch.setattr(android.shutil, "which", lambda _name: "adb")
    monkeypatch.setattr(android, "run_capture", lambda *a, **k: (
        0, b"List of devices attached\n9H9DS4PNZ9S8HM6H\tdevice\nemulator-5554\toffline\n\n", b""))
    assert android.devices_from_env() == {"phone": "9H9DS4PNZ9S8HM6H"}


def _archive(version: str, files: dict) -> "zipfile.ZipFile":
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"Jarvis-x/{name}", text)
        z.comment = version.encode()
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def test_update_installs_once_and_remembers_the_version(tmp_path, monkeypatch):
    from jarvis import updater

    root = tmp_path
    (root / "jarvis").mkdir()
    (root / "jarvis" / "old.py").write_text("x")
    (root / "pyproject.toml").write_text("[project]\nname='jarvis'\n")
    (root / ".env").write_text("ANTHROPIC_API_KEY=keep\n")
    archive = _archive("abc123", {"pyproject.toml": "[project]\nname='jarvis'\n", "jarvis/__main__.py": "print(1)",
                                  ".env": "ANTHROPIC_API_KEY=lost\n"})
    monkeypatch.setattr(updater, "download", lambda: archive)
    assert updater.newer(root) is archive
    updater.install(root, archive, say=lambda _m: None)
    assert (root / "jarvis" / "__main__.py").exists() and not (root / "jarvis" / "old.py").exists()
    assert (root / ".env").read_text() == "ANTHROPIC_API_KEY=keep\n"
    assert updater.installed_version(root) == "abc123"
    assert updater.newer(root) is None  # nothing new until GitHub has another commit


def test_update_waits_until_the_restarted_app_answers(monkeypatch, capsys):
    """Else a `jarvis app` typed right after it starts a second Jarvis beside the restarting one."""
    import io
    import time

    from jarvis import updater

    monkeypatch.setenv("JARVIS_WEB_TOKEN", "t")
    asked, answers = [], iter([False, False, True])
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout: asked.append(req) or io.BytesIO(b"{}"))
    monkeypatch.setattr(updater, "answering", lambda port: next(answers))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    updater.restart_running_app()
    assert asked[0].full_url.endswith("/api/restart") and "работи с новата версия" in capsys.readouterr().out


def test_bulgarian_voice_reads_every_answer_and_markdown_is_cleaned():
    from jarvis.tts import ENGLISH_VOICE, clean, pick_voice

    assert pick_voice("Good evening, sir.", "bg-BG-BorislavNeural") == "bg-BG-BorislavNeural"  # always Bulgarian
    assert pick_voice("Dobar vecher, sar.", "bg-BG-BorislavNeural") == "bg-BG-BorislavNeural"
    assert pick_voice("Good evening, sir.", "de-DE-ConradNeural") == ENGLISH_VOICE
    assert clean("**Готово** виж [тук](https://x.y) ```код```") == "Готово виж тук"


def test_answer_parts_keep_the_voice_of_the_whole_answer(monkeypatch):
    from jarvis import tts

    used = []
    monkeypatch.setattr(tts, "edge", lambda text, voice: used.append(voice) or b"mp3")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    answer = "Добър вечер, сър. Срещата е в 15:00. OK."
    assert tts.synthesize("OK.", "bg-BG-BorislavNeural", sample=answer) == b"mp3"
    assert used == ["bg-BG-BorislavNeural"]  # a short English part of a Bulgarian answer stays in Bulgarian


def test_tools_and_system_prompt_are_cached_but_not_the_clock(settings, ctx, registry):
    from conftest import FakeClient, response, text_block
    from jarvis.brain import Jarvis

    client = FakeClient([response(text_block("Добре."))])
    Jarvis(settings, ctx.store, registry, lambda s: True, client=client).ask("здравей")
    request = client.requests[0]
    assert request["cache_control"] == {"type": "ephemeral"}
    cached, clock = request["system"]
    assert cached["cache_control"] == {"type": "ephemeral"} and "Current local time" not in cached["text"]
    assert clock["text"].startswith("Current local time") and "cache_control" not in clock


def test_an_answer_never_fails_because_of_caching(settings, ctx, registry):
    import anthropic

    from conftest import FakeClient, response, text_block
    from jarvis.brain import Jarvis

    client = FakeClient([response(text_block("Добре."))])
    original = client._create

    def create(**kwargs):
        if "cache_control" in kwargs:
            err = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
            Exception.__init__(err, "cache_control: not supported here")
            raise err
        return original(**kwargs)

    client.beta.messages.create = create
    assert Jarvis(settings, ctx.store, registry, lambda s: True, client=client).ask("здравей") == "Добре."


def test_jarvis_waits_for_a_pause_before_acting(settings, ctx, registry, monkeypatch):
    from test_hub import call, make_hub

    from jarvis.config import silence_seconds

    assert silence_seconds() == 2.0
    for value, seconds in [("3", 3.0), ("1,5", 1.5), ("0", 0.5), ("60", 10.0), ("много", 2.0)]:
        monkeypatch.setenv("JARVIS_SILENCE", value)
        assert silence_seconds() == seconds
    _hub, port = make_hub(settings, ctx, registry, [])
    assert json.loads(call(port, "/api/me")[1])["silence"] == 2.0


def test_jarvis_is_told_to_claim_only_what_his_tools_did(settings, ctx, registry):
    client = FakeClient([response(text_block("Добре."))])
    Jarvis(settings, ctx.store, registry, Approver(), client=client).ask("работи сам до 6:30 и ми пиши доклад")
    instructions = system_text(client.requests[0])
    assert "only when a tool call" in instructions and "Between messages you do nothing on your own" in instructions
    assert "keep working while the user" in registry.tools["schedule_job"].description


def test_asked_to_act_he_acts_instead_of_only_talking(settings, ctx, registry):
    from jarvis.brain import ACT_CHECK

    settings.act_check = True
    client = FakeClient([
        response(text_block("Добре, ще ги прегледам.")),  # only talk: he gets one more look
        response(tool_block("list_tasks", {}), stop="tool_use"),
        response(text_block("Нямаш отворени задачи.")),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("pogledni mi zadachite") == "Нямаш отворени задачи."
    assert len(client.requests) == 3 and client.requests[1]["messages"][-1]["content"] == ACT_CHECK
    assert ACT_CHECK not in str(ctx.store.history("main", 10))  # the nudge is not kept in the conversation

    client.responses = [response(tool_block("list_tasks", {}), stop="tool_use"), response(text_block("Готово."))]
    client.requests.clear()
    assert jarvis.ask("виж ми задачите") == "Готово." and len(client.requests) == 2  # he acted: no nudge

    for text in ["Защо небето е синьо?", "благодаря ти", "здрасти джарвис"]:  # knowing or small talk needs no tools
        client.responses = [response(text_block("Добре."))]
        client.requests.clear()
        jarvis.ask(text)
        assert len(client.requests) == 1, text

    ctx.jarvis = jarvis
    client.responses = [response(text_block("NOTHING"))]
    client.requests.clear()
    ctx.scheduler.heartbeat()  # a check-in with nothing to say is not pushed to act
    assert len(client.requests) == 1


def test_an_empty_answer_is_never_reported_as_done(settings, ctx, registry):
    client = FakeClient([response()])
    answer = Jarvis(settings, ctx.store, registry, Approver(), client=client).ask("пусни ми нещо")
    assert answer != "Готово." and "Не успях" in answer


def test_at_the_step_limit_he_says_what_he_did_and_what_is_left(settings, ctx, registry):
    from jarvis.brain import WRAP_UP

    settings.max_tool_rounds = 2
    client = FakeClient([response(tool_block("list_tasks", {}), stop="tool_use"),
                         response(tool_block("list_tasks", {}, id="tu_2"), stop="tool_use"),
                         response(text_block("Прегледах задачите два пъти; остава да ги подредя."))])
    answer = Jarvis(settings, ctx.store, registry, Approver(), client=client).ask("подреди ми задачите")
    assert answer.startswith("(Стигнах края на стъпките") and "остава да ги подредя" in answer
    assert client.requests[2]["messages"][-1]["content"][-1] == {"type": "text", "text": WRAP_UP}
