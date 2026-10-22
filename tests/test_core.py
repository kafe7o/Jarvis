from __future__ import annotations

import json
from datetime import datetime

from conftest import Approver, FakeClient, response, system_text, text_block, tool_block

from jarvis import brain as brain_module
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


def test_a_file_is_deleted_but_never_an_allowed_folder_itself(tmp_path, registry):
    target = tmp_path / "old.txt"
    target.write_text("x")
    out, err = registry.run("delete_path", {"path": str(target)}, Approver())
    assert not err and not target.exists()
    out, err = registry.run("delete_path", {"path": str(tmp_path)}, Approver())
    assert err and "allowed root" in out and tmp_path.exists()


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
    assert first["model"] == "local"  # the best model on the laptop
    names = {t["name"] for t in first["tools"]}
    assert {"add_task", "add_reminder", "run_shell", "google_search"} <= names
    assert "make_call" not in names  # only the tools the request is about
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


def test_system_prompt_is_fixed_for_a_turn(settings, ctx, registry):
    from conftest import FakeClient, response, text_block, tool_block
    from jarvis.brain import Jarvis

    client = FakeClient([
        response(tool_block("remember", {"topic": "t", "fact": "нов факт"}), stop="tool_use"),
        response(text_block("Запомних.")),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=client)
    assert jarvis.ask("запомни") == "Запомних."
    first, second = client.requests
    assert first["system"] == second["system"]  # the new fact did not change the prompt mid-turn


def test_phone_is_found_over_usb_without_settings(monkeypatch):
    from jarvis.plugins import android

    monkeypatch.delenv("JARVIS_ADB_DEVICES", raising=False)
    monkeypatch.setattr(android.shutil, "which", lambda _name: "adb")
    monkeypatch.setattr(android, "run_capture", lambda *a, **k: (
        0, b"List of devices attached\n9H9DS4PNZ9S8HM6H\tdevice\nemulator-5554\toffline\n\n", b""))
    assert android.devices_from_env() == {"phone": "9H9DS4PNZ9S8HM6H"}


def _archive(version: str, files: dict):
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
    monkeypatch.setattr(updater, "latest_version", lambda: "")  # GitHub's quick answer unavailable: download
    assert updater.newer(root) is archive
    updater.install(root, archive, say=lambda _m: None)
    assert (root / "jarvis" / "__main__.py").exists() and not (root / "jarvis" / "old.py").exists()
    assert (root / ".env").read_text() == "ANTHROPIC_API_KEY=keep\n"
    assert updater.installed_version(root) == "abc123"
    assert updater.newer(root) is None  # nothing new until GitHub has another commit


def test_a_check_with_nothing_new_downloads_nothing(tmp_path, monkeypatch):
    import io

    from jarvis import updater

    (tmp_path / updater.VERSION_FILE).write_text("a" * 40)
    asked = []

    def urlopen(req, timeout=0):
        asked.append(req.full_url)
        return io.BytesIO(b"a" * 40)

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updater, "download", lambda: (_ for _ in ()).throw(AssertionError("downloaded")))
    assert updater.newer(tmp_path) is None
    assert asked == [f"{updater.API}/commits/main"]
    # GitHub does not answer (no connection, its hourly limit): the background check downloads nothing either
    monkeypatch.setattr(updater, "latest_version", lambda: "")
    assert updater.newer(tmp_path, known_only=True) is None


def test_auto_update_checks_again_and_again_by_itself(settings, ctx, registry, monkeypatch):
    """The laptop missed an evening's fixes: it checked every 6 hours, and a laptop that slept pushed the check
    back. Now it checks every half hour by the wall clock (sped up here), always waiting for Jarvis to be idle."""
    from jarvis.hub import Hub

    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=FakeClient([]))
    hub = Hub(ctx, "secret", port=0)
    checks = []
    monkeypatch.setattr(hub, "update_now", lambda wait_idle=False: checks.append(wait_idle) or False)
    hub.auto_update(first_wait=0.01, every=0.02, step=0.01, rounds=3).join(timeout=5)
    assert checks == [True, True, True]


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
    assert pick_voice("Good evening, sir.", "en-US-AndrewMultilingualNeural") == "en-US-AndrewMultilingualNeural"
    assert clean("**Готово** виж [тук](https://x.y) ```код```") == "Готово виж тук"


def test_answer_parts_keep_the_voice_of_the_whole_answer(monkeypatch):
    from jarvis import tts

    used = []
    monkeypatch.setattr(tts, "edge", lambda text, voice: used.append(voice) or b"mp3")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    answer = "Добър вечер, сър. Срещата е в 15:00. OK."
    assert tts.synthesize("OK.", "bg-BG-BorislavNeural", sample=answer) == b"mp3"
    assert used == ["bg-BG-BorislavNeural"]  # a short English part of a Bulgarian answer stays in Bulgarian


def test_the_clock_is_apart_from_the_rest_of_the_prompt(settings, ctx, registry):
    from conftest import FakeClient, response, text_block
    from jarvis.brain import Jarvis

    client = FakeClient([response(text_block("Добре."))])
    Jarvis(settings, ctx.store, registry, lambda s: True, client=client).ask("здравей")
    fixed, clock = client.requests[0]["system"]
    assert "Current local time" not in fixed["text"] and clock["text"].startswith("Current local time")


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
    client = FakeClient([response(text_block("Добре.")), response(text_block("Добре."))])  # a mission: one more look
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


def test_jarvis_learns_a_lesson_from_a_failed_task_and_follows_it(settings, ctx, registry):
    from conftest import FakeClient, response, system_text, text_block, tool_block
    from jarvis import core_files
    from jarvis.brain import Jarvis

    settings.learn = True
    client = FakeClient([
        response(tool_block("run_shell", {"command": "nosuchprogram"}), stop="tool_use"),
        response(text_block("Не успях, сър.")),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=client)
    jarvis.reflect_later = lambda *args: None  # looked back at below, not in the background
    steps = []
    jarvis._loop([{"role": "user", "content": "пусни програмата"}], lambda s: True, steps=steps)
    assert steps and steps[0][0] == "run_shell"
    client.responses.append(response(text_block("Когато програма липсва, първо провери дали е инсталирана.")))
    lesson = jarvis.reflect("пусни програмата", [("run_shell", {}, "not found", True)], "Не успях, сър.")
    assert lesson and "провери дали е инсталирана" in core_files.read(settings.home, "LESSONS.md")
    client.responses.append(response(text_block("NONE")))
    assert jarvis.reflect("пак", [("run_shell", {}, "not found", True)], "Не успях.") is None  # nothing new
    client.responses.append(response(text_block("Добре.")))
    jarvis.ask("здрасти")
    assert "провери дали е инсталирана" in system_text(client.requests[-1])  # every request carries the lessons


def test_review_myself_shows_what_failed_and_learn_keeps_lessons(settings, ctx, registry):
    from conftest import Approver
    from jarvis.brain import Jarvis

    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver())
    ctx.jarvis.log_activity(None, "Jarvis", "click_text", {"target": "OK"}, True)
    ctx.jarvis.log_activity(None, "Jarvis", "click_text", {"target": "OK"}, False)
    out, err = registry.run("review_myself", {}, Approver())
    assert not err and "click_text (1 failed of 2)" in out and "failed: 1" in out
    out, err = registry.run("learn", {"lesson": "Когато бутонът няма име, ползвай look_at_screen."}, Approver())
    assert not err and "kept" in out
    out, _ = registry.run("learn", {"lesson": "когато бутонът няма име, ползвай look_at_screen"}, Approver())
    assert "Already" in out


def test_a_task_that_struggled_then_worked_becomes_a_recipe_he_follows_next_time(settings, ctx, registry):
    from conftest import Approver
    from jarvis import recipes

    settings.learn = True
    struggled = [("click_text", {"target": "Upload"}, "no such text on the screen", True),
                 ("press_keys", {"keys": "ctrl+o"}, "pressed", False),
                 ("control_input", {"text": "C:\\x.pdf"}, "typed", False)]
    assert recipes.worth_it(struggled, "Готово, качих файла в NotebookLM.")
    assert not recipes.worth_it(struggled[1:], "Готово, качих файла.")  # went smoothly: nothing new to keep
    assert not recipes.worth_it(struggled[::-1], "Не успях да кача файла.")  # ended badly: a lesson, not a recipe
    client = FakeClient([response(text_block(
        "Качване на файлове в NotebookLM през браузъра\n1. Отвори бележника и натисни „Добави източник“.\n"
        "2. Натисни Ctrl+O и напиши пълния път на файла.\n3. Провери екрана, че файлът се вижда.\n"
        "Избягвай: да търсиш бутон Upload по име."))])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    assert jarvis.make_recipe("качи тези pdf-и в notebooklm в нов бележник", struggled, "Готово, качих файла.") \
        == "Качване на файлове в NotebookLM през браузъра"
    assert "NotebookLM" in recipes.listing(ctx.store)
    assert recipes.matching(ctx.store, "kachi tezi pdf-i v notebooklm v nov belejnik")  # Latin typing finds it too
    assert not recipes.matching(ctx.store, "какво е времето в София")
    client.responses.append(response(text_block("Готово, качих ги.")))
    jarvis.ask("качи тези файлове в notebooklm")
    sent = client.requests[-1]["messages"][-1]["content"]
    assert "Натисни Ctrl+O" in sent and "Never mention them" in sent  # the recipe goes with the request
    # another kind of task shares only the everyday words ("отвори", "приложенията"): no recipe for it
    assert not recipes.matching(ctx.store, "отвори task manager, сложи end task на claude и го отвори от приложенията")
    assert all("Ctrl+O" not in r["content"] for r in ctx.store.history("main"))  # but not into the saved talk
    ctx.jarvis = jarvis
    out, err = registry.run("review_myself", {}, Approver())
    assert not err and "Качване на файлове в NotebookLM" in out


def test_a_go_ahead_continues_his_own_plan_and_a_correction_makes_him_act(settings, ctx, registry):
    """The owner's laptop at 4 a.m.: "Отварям Task Manager, спирам Claude и го пускам" - "davai de" - and the brain
    went back to the NotebookLM task from earlier in the conversation; told "на компютъра, не на телефона" it
    answered "Разбрах, отварям..." and did nothing."""
    from jarvis import router
    from jarvis.brain import ACT_CHECK, GO_AHEAD_HINT

    for text in ["davai de", "aide davai", "давай", "ок, действай", "Добре, продължи.", "go ahead"]:
        assert router.go_ahead(text), text
    for text in ["давай да видим пощата", "ok, now open the mail", "давай ми новините всяка сутрин"]:
        assert not router.go_ahead(text), text
    settings.act_check = True
    client = FakeClient([
        response(text_block("Разбрах, спирам Claude и го пускам пак.")),  # said, not done: one more look
        response(tool_block("list_tasks", {}), stop="tool_use"),
        response(text_block("Готово, Claude е пуснат.")),
    ])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    jarvis.ask("ideyata beshe da go napravish na kompyutura ne na telefona")  # a correction: act, don't tell
    assert client.requests[1]["messages"][-1]["content"] == ACT_CHECK
    client.responses = [response(text_block("Ще започна с NotebookLM.")), response(tool_block("list_tasks", {}), stop="tool_use"),
                        response(text_block("Готово."))]
    client.requests.clear()
    jarvis.ask("davai de")
    sent = client.requests[0]["messages"][-1]["content"]
    assert sent.endswith(GO_AHEAD_HINT) and "exactly what your previous message said" in sent
    assert client.requests[1]["messages"][-1]["content"] == ACT_CHECK  # talk only: the go-ahead means act
    assert all(GO_AHEAD_HINT not in r["content"] for r in ctx.store.history("main"))


def test_praise_keeps_the_way_the_last_task_was_done(settings, ctx, registry):
    from jarvis import router

    assert router.praises("евала") and router.praises("Браво бе!") and router.praises("tochno taka")
    assert not router.praises("евала, сега отвори пощата") and not router.praises("браво на теб, но пак не работи")
    settings.learn = True
    client = FakeClient([response(tool_block("list_tasks", {}), stop="tool_use"), response(text_block("Ето ги.")),
                         response(text_block("Благодаря, сър."))])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    kept = []
    jarvis.recipe_later = lambda request, steps, answer: kept.append(request)
    jarvis.ask("покажи ми задачите")
    assert kept == []  # it went smoothly, so nothing was kept on its own
    jarvis.ask("евала")
    assert kept == ["покажи ми задачите"]  # the owner's praise says the way was right: it is kept


def test_update_reads_a_private_repository_with_the_owners_token(monkeypatch):
    """When the repository is private the public zip is gone (404); the API zip with the token works instead."""
    import io
    import urllib.error
    import zipfile

    import pytest

    from jarvis import updater

    seen = []

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("Authorization"), req.get_header("Accept")))
        if "api.github.com" not in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))
        if "/commits/" in req.full_url:
            return io.BytesIO(b"abc123\n")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:  # the API archive here carries no commit id in its comment
            z.writestr("kafe7o-Jarvis-abc123/jarvis/__main__.py", "print(1)")
        return io.BytesIO(buf.getvalue())

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="GITHUB_TOKEN"):
        updater.download()
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    archive = updater.download()
    assert updater.version_of(archive) == "abc123"
    assert seen[-2][0] == "https://api.github.com/repos/kafe7o/Jarvis/zipball/main"
    assert seen[-2][1] == "Bearer ghp_test" and seen[-1][2] == "application/vnd.github.sha"


def test_a_job_on_the_web_gets_the_fast_lane_a_short_brief_and_a_dozen_tools(settings, ctx, registry):
    """Seventy tools and the full brief made every step of a web job ~20k tokens and up to 45 s on a free brain."""
    client = FakeClient([response(text_block("Доларът е 1.67 лв."), stop="end_turn")])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    jarvis.ask("Отвори сайта на БНБ в браузъра и ми кажи курса на долара днес")
    request = client.requests[0]
    names = {t["name"] for t in request["tools"]}
    assert names <= set(brain_module.WEB_TOOLS) and {"open_page", "click_text", "request_approval"} <= names
    assert "This is a job on the web" in system_text(request) and "team of specialists" not in system_text(request)

    client.responses.append(response(text_block("Готово."), stop="end_turn"))
    jarvis.ask("Виж в браузъра пощата ми")  # the mail needs its own tools: the full agent
    names = {t["name"] for t in client.requests[-1]["tools"]}
    assert {"run_shell", "gmail_search"} <= names and "team of specialists" in system_text(client.requests[-1])


def test_hearing_a_voice_plays_exactly_that_voice(monkeypatch):
    from jarvis import tts

    used = []
    monkeypatch.setattr(tts, "edge", lambda text, voice: used.append(voice) or b"mp3")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")  # „Чуй“ plays the picked voice, not ElevenLabs
    monkeypatch.setattr(tts, "elevenlabs", lambda text: (_ for _ in ()).throw(AssertionError("not ElevenLabs")))
    assert tts.synthesize("Hello, sir.", "de-DE-ConradNeural", exact=True) == b"mp3" and used == ["de-DE-ConradNeural"]
    assert all(tts.voice_ok(v) for v, _n, _a in tts.VOICES)
    assert not tts.voice_ok("Калина") and not tts.voice_ok("bg-BG-x; rm")


def test_an_old_edge_tts_is_updated_once_and_the_voice_retried(monkeypatch):
    import subprocess

    from jarvis import tts

    calls, ran = [], []
    def edge(text, voice):
        calls.append(voice)
        if len(calls) == 1:
            raise RuntimeError("403 Invalid response status")  # Microsoft refuses the old sign-in
        return b"mp3"
    monkeypatch.setattr(tts, "edge", edge)
    monkeypatch.setattr(tts, "edge_version", lambda: (7, 2, 8))
    monkeypatch.setattr(tts, "FRESHENED", {"done": False})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert tts.synthesize("Здравей.", "bg-BG-KalinaNeural") == b"mp3"
    assert calls == ["bg-BG-KalinaNeural"] * 2 and ran and "edge-tts>=7.3.1" in ran[0]
    assert tts.freshen_edge() is False  # once per run


def test_a_cloud_brain_left_in_the_settings_means_the_laptop(monkeypatch):
    from jarvis import local
    from jarvis.brain import as_local
    from jarvis.config import brain_setting

    assert brain_setting("mistral") == brain_setting("auto") == brain_setting("") == "local"
    assert brain_setting("local:qwen3.5:4b") == "local:qwen3.5:4b" and brain_setting("local:a@b.c") == "local"
    monkeypatch.setattr(local, "_seen", {"at": float("inf"), "models": ["qwen3.5:4b"]})
    assert as_local("qwen3.5:4b") == "local:qwen3.5:4b" and as_local("qwen3.5") == "local:qwen3.5"
    assert as_local("gemini-3.5-flash") == "local" and as_local(None) == "local"


def test_every_tool_is_in_a_group_an_account_can_be_given(registry):
    from jarvis import accounts

    groups = {g[0] for g in accounts.GROUPS} | {"core"}
    assert {n: t.group for n, t in registry.tools.items() if t.group not in groups} == {}


def test_the_newest_video_of_a_channel_comes_from_its_own_feed(monkeypatch):
    from jarvis.plugins import daily

    pages = {"results": '"channelRenderer":{"channelId":"UCabcdefghijklmnopqrstuv","title"',
             "feeds": "<entry><yt:videoId>NEWVIDEO123</yt:videoId><title>Ново &amp; свежо</title></entry>"
                      "<entry><yt:videoId>OLDVIDEO123</yt:videoId><title>Старо</title></entry>"}
    monkeypatch.setattr(daily, "fetch", lambda url, *a, **k: next(v for key, v in pages.items() if key in url))
    assert daily.newest_video("Хараламби Добрев") == ("NEWVIDEO123", "Ново & свежо")


def test_a_video_for_a_phone_that_is_not_connected_plays_on_the_laptop(registry, monkeypatch):
    from jarvis.plugins import daily

    opened = []
    monkeypatch.setattr(daily, "find_video", lambda q: ("abcdefghijk", "Песен"))
    monkeypatch.setattr(daily.browsers, "open_url", lambda url: opened.append(url) or "Brave")
    monkeypatch.delenv("JARVIS_ADB_DEVICES", raising=False)
    out, err = registry.run("play_youtube", {"query": "песен", "device": "phone"}, Approver())
    assert not err and opened == ["https://www.youtube.com/watch?v=abcdefghijk"] and "this computer" in out


def test_jarvis_remembers_what_the_owner_says_about_his_life_without_being_told(settings, ctx, registry):
    from conftest import FakeClient, response, text_block
    from jarvis import brain
    from jarvis.brain import Jarvis

    settings.learn = True
    client = FakeClient([response(text_block("семейство: Сестра му се казва Мария\nкола: Кара синя Тойота"))])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=client)
    kept = jarvis.notice("сестра ми се казва Мария, а аз карам синя тойота", "Разбрах, сър.")
    assert kept == ["семейство: Сестра му се казва Мария", "кола: Кара синя Тойота"]
    client.responses.append(response(text_block("семейство: Сестрата му се казва Мария\nNONE")))
    assert jarvis.notice("сестра ми Мария пак", "Да.") == []  # already known, in other words
    assert len(ctx.store.query("SELECT id FROM facts")) == 2
    assert not brain.PERSONAL.search("пусни ми Back in Black") and brain.PERSONAL.search("obicham kafe bez zahar")


def test_with_the_memory_model_the_brain_gets_the_tools_closest_to_the_request(registry, monkeypatch):
    from jarvis import brain, local

    tools = registry.definitions(None)
    groups = {n: (t.group or "agent") for n, t in registry.tools.items()}
    monkeypatch.setattr(local, "similar", lambda q, texts, top=8, post=None, floor=0.45:
                        [i for i, t in enumerate(texts) if t.startswith(("weather:", "set_volume:"))][:top])
    picked = {t["name"] for t in brain.select_tools(tools, groups, "какво е времето утре")}
    assert len(picked) <= brain.TOOLS_MAX and {"weather", "set_volume", "remember", "open_page"} <= picked
    assert "build_website" not in picked
    assert brain.CORE_TOOLS <= set(registry.tools)  # every core tool exists
    monkeypatch.setattr(local, "similar", lambda *a, **k: [])
    assert len(brain.select_tools(tools, groups, "какво е времето утре")) > brain.TOOLS_MAX  # no memory model: all


def test_the_vault_keeps_each_day_and_what_jarvis_knows_and_reads_it_back(settings, ctx, registry):
    from conftest import FakeClient, response, system_text, text_block
    from jarvis import journal
    from jarvis.brain import Jarvis

    client = FakeClient([response(text_block("Записах, сър.")), response(text_block("Мария, сър."))])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=client)
    jarvis.ask("сестра ми Мария има рожден ден на 5 май", route=None)
    daily = list((settings.vault / "daily").glob("*.md"))
    assert len(daily) == 1 and "сестра ми Мария" in daily[0].read_text(encoding="utf-8")
    journal.file_fact(settings.vault, "семейство", "Сестра му Мария има рожден ден на 5 май")
    note = (settings.vault / "wiki" / "Семейство.md").read_text(encoding="utf-8")
    assert "5 май" in note and "[[_master-index]]" in note
    assert "[[Семейство]]" in (settings.vault / "wiki" / "_master-index.md").read_text(encoding="utf-8")
    jarvis.ask("кога е рожденият ден на Мария", route=False)
    assert "Мария има рожден ден на 5 май" in system_text(client.requests[-1])  # read back from the vault


def test_what_jarvis_remembers_lands_in_the_vault_too(registry, settings):
    out, err = registry.run("remember", {"topic": "кола", "fact": "Кара синя Тойота"}, Approver())
    assert not err and "синя Тойота" in (settings.vault / "wiki" / "Кола.md").read_text(encoding="utf-8")
