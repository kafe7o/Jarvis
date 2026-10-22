from __future__ import annotations

import json
import threading
import time
import urllib.request

from conftest import Approver, FakeClient, response, text_block, tool_block

from jarvis.brain import Jarvis
from jarvis.hub import Hub


def call(port, path, body=None, token="secret"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode()


def make_hub(settings, ctx, registry, responses):
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(False), client=FakeClient(responses))
    hub = Hub(ctx, "secret", port=0)
    server = hub.serve()
    return hub, server.server_address[1]


def test_web_app_serves_page_but_api_needs_login(settings, ctx, registry):
    hub, port = make_hub(settings, ctx, registry, [])
    status, html = call(port, "/")
    assert status == 200 and "J.A.R.V.I.S." in html
    try:
        call(port, "/api/chats", token="wrong")
        raise AssertionError("expected 401")
    except urllib.error.HTTPError as err:
        assert err.code == 401


def test_web_confirmation_round_trip(settings, ctx, registry, tmp_path):
    target = tmp_path / "note.txt"
    hub, port = make_hub(settings, ctx, registry, [
        response(tool_block("write_file", {"path": str(target), "content": "здрасти"}), stop="tool_use"),
        response(text_block("Записах го."), stop="end_turn"),
    ])
    answers = {}
    asker = threading.Thread(target=lambda: answers.update(json.loads(call(port, "/api/ask", {"text": "запиши"})[1])))
    asker.start()
    confirm, after = None, 0
    while confirm is None:  # progress events (the level, the tool) may come first
        events = json.loads(call(port, f"/api/events?after={after}")[1])["events"]
        confirm = next((e for e in events if e["kind"] == "confirm"), None)
        after = max([after] + [e["id"] for e in events])
    assert "note.txt" in confirm["text"]
    call(port, "/api/confirm", {"id": confirm["id"], "yes": True})
    asker.join(10)
    assert answers["answer"] == "Записах го." and target.read_text() == "здрасти"


def test_remote_device_tools(settings, ctx, registry, tmp_path, monkeypatch):
    hub, port = make_hub(settings, ctx, registry, [])
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "node"))
    monkeypatch.setenv("JARVIS_FILE_ROOTS", str(tmp_path))
    (tmp_path / "laptop-file.txt").write_text("от лаптопа")
    from jarvis.node import NodeClient

    node = NodeClient(f"http://127.0.0.1:{port}", "laptop", "secret")
    threading.Thread(target=node.run_forever, daemon=True).start()
    for _ in range(50):
        if "laptop__read_file" in registry.tools:
            break
        time.sleep(0.1)
    tool = registry.tools["laptop__read_file"]
    assert tool.description.startswith("On the device 'laptop'")
    assert registry.tools["laptop__run_shell"].confirm  # risky tools keep their confirmation
    out, err = registry.run("laptop__read_file", {"path": str(tmp_path / "laptop-file.txt")}, Approver())
    assert (out, err) == ("от лаптопа", False)
    out, err = registry.run("laptop__run_shell", {"command": "echo hi"}, Approver(False))
    assert err and "declined" in out
    assert json.loads(call(port, "/api/devices")[1])["devices"][0]["online"]


def test_tasks_tab_shows_what_runs_now_and_what_comes_next(settings, ctx, registry):
    from jarvis import routines

    hub, port = make_hub(settings, ctx, registry, [])
    routines.set_routine(ctx.store, "morning", True, "08:00")
    ctx.store.insert("reminders", text="Провери цената на тока", at="2099-01-01T09:00:00", channels="agent")
    run_id, run = hub.track("По график", "Сутрешен брифинг")
    run["step"] = "Чете календара"
    now = json.loads(call(port, "/api/now")[1])
    assert now["running"][0]["text"] == "Сутрешен брифинг" and now["running"][0]["step"] == "Чете календара"
    kinds = {r["text"]: r["kind"] for r in now["upcoming"]}
    assert kinds == {"Сутрешен брифинг": "routine", "Провери цената на тока": "job"}
    hub.board.finish(run_id)
    assert json.loads(call(port, "/api/now")[1])["running"] == []
    job = next(r for r in now["upcoming"] if r["kind"] == "job")
    import urllib.error

    try:  # a routine is switched off in its own tab, never deleted here
        routine = next(r for r in now["upcoming"] if r["kind"] == "routine")
        call(port, f"/api/jobs/{routine['id']}/delete", {})
        raise AssertionError("a routine was deleted")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
    assert json.loads(call(port, f"/api/jobs/{job['id']}/delete", {})[1]) == {"deleted": job["id"]}
    assert all(r["kind"] != "job" for r in json.loads(call(port, "/api/now")[1])["upcoming"])
    try:
        call(port, f"/api/jobs/{job['id']}/run", {})
        raise AssertionError("a deleted job ran")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    usage = json.loads(call(port, "/api/usage")[1])
    assert usage["today"] == {"requests": 0, "tokens": 0} and len(usage["days"]) == 14


def test_the_app_gets_its_answer_as_an_event_even_after_the_connection_dropped(settings, ctx, registry):
    hub, port = make_hub(settings, ctx, registry, [response(text_block("Готово, сър."), stop="end_turn")])
    chat = json.loads(call(port, "/api/chats", {})[1])["chat"]
    start = json.loads(call(port, "/api/events?after=-1")[1])
    job = json.loads(call(port, f"/api/chats/{chat['id']}/ask", {"text": "здрасти", "background": True})[1])["job"]
    found, after = None, start["last"]
    while found is None:  # the request itself returned at once; the answer comes when the work is done
        data = json.loads(call(port, f"/api/events?after={after}")[1])
        found = next((e for e in data["events"] if e["kind"] == "answer" and e["job"] == job), None)
        after = max([after] + [e["id"] for e in data["events"]])
    assert found["answer"] == "Готово, сър." and not found["error"] and data["boot"] == start["boot"]


def test_event_ids_keep_growing_after_a_restart(settings, ctx, registry):
    from jarvis.hub import EventLog

    before = EventLog()
    seen = before.add("notify", text="преди рестарта")
    time.sleep(0.01)
    after = EventLog()  # a new process
    assert after.add("notify", text="след рестарта") > seen  # an open window that saw ``seen`` still gets it
    assert [e["text"] for e in after.after(seen, wait=0)] == ["след рестарта"]


def test_tasks_are_done_one_after_another_and_stop_is_heard_at_once(settings, ctx, registry):
    from jarvis.brain import STOPPED
    from jarvis.tools import Tool

    inside, release = threading.Event(), threading.Event()
    registry.add(Tool("slow_job", "A long piece of work.", lambda: (inside.set(), release.wait(10)) and "свърших",
                      {"type": "object", "properties": {}}))
    hub, _port = make_hub(settings, ctx, registry, [
        response(tool_block("slow_job", {}), stop="tool_use"), response(text_block("Първото е готово.")),
        response(text_block("Второто е готово.")),
    ])
    client = ctx.jarvis.local
    said = []
    first = threading.Thread(target=lambda: said.append(hub.ask("направи първото")))
    first.start()
    assert inside.wait(10)
    second = threading.Thread(target=lambda: said.append(hub.ask("направи второто")))
    second.start()
    time.sleep(0.3)
    assert len(client.requests) == 1  # the second waits: it does not start beside the first
    release.set()
    first.join(10), second.join(10)
    assert said == ["Първото е готово.", "Второто е готово."]  # in the order they were asked

    inside.clear(), release.clear()
    client.responses[:] = [response(tool_block("slow_job", {}), stop="tool_use"), response(text_block("Не трябва."))]
    said.clear()
    first = threading.Thread(target=lambda: said.append(hub.ask("направи трето")))
    first.start()
    assert inside.wait(10)
    second = threading.Thread(target=lambda: said.append(hub.ask("и четвърто")))
    second.start()
    time.sleep(0.3)
    assert hub.ask("спри всичко") == STOPPED  # heard at once, while the third is still working
    release.set()
    first.join(10), second.join(10)
    assert sorted(said) == sorted([STOPPED, "Не го почнах: каза ми да спра."])
    assert len(client.requests) == 4  # the third stopped at its next step; the fourth never started


def test_the_tasks_tab_runs_a_scheduled_job_right_now(settings, ctx, registry):
    import time

    from jarvis import notices

    hub, port = make_hub(settings, ctx, registry, [response(text_block("Токът е 0.20 лв."), stop="end_turn")])
    ctx.scheduler.start() if hasattr(ctx.scheduler, "start") else None
    ctx.store.insert("reminders", text="Провери цената на тока", at="2099-01-01T09:00:00", channels="agent")
    job = next(r for r in json.loads(call(port, "/api/now")[1])["upcoming"] if r["kind"] == "job")
    assert json.loads(call(port, f"/api/jobs/{job['id']}/run", {})[1]) == {"started": job["id"]}
    for _ in range(100):
        if notices.unseen(ctx.store):
            break
        time.sleep(0.05)
    assert notices.unseen(ctx.store)[0]["text"] == "Задачата „Провери цената на тока“ мина: Токът е 0.20 лв."


def test_a_task_dropped_in_the_remote_folder_is_asked_as_the_owner(settings, ctx, registry, tmp_path):
    from jarvis import remote

    hub, _ = make_hub(settings, ctx, registry, [
        response(tool_block("list_tasks", {}), stop="tool_use"),
        response(text_block("Нямаш задачи."), stop="end_turn"),
    ])
    owner = hub.accounts.create("owner@example.com", "Анастас", "123456", role="owner")
    where = remote.folder(tmp_path)
    where.mkdir()
    (where / "test1.txt").write_text("какви задачи имам?", encoding="utf-8")
    remote.watch(hub, tmp_path, every=0.01, rounds=1).join(30)
    answer = (where / "test1.answer.txt").read_text(encoding="utf-8")
    assert "Нямаш задачи." in answer and "Първа стъпка:" in answer and "(след" in answer
    assert remote.waiting(where) == [] and not (where / "test1.working.txt").exists()
    chat = next(c for c in hub.accounts.chats(owner.id) if c["title"] == remote.CHAT + ": test1")
    assert any("Нямаш задачи." in m["content"] for m in hub.accounts.messages(chat["id"]))


def test_a_remote_task_cut_off_by_a_restart_is_asked_again_once(tmp_path):
    from jarvis import remote

    where = remote.folder(tmp_path)
    where.mkdir()
    (where / "a.working.txt").write_text("задача", encoding="utf-8")
    (where / "b.again.working.txt").write_text("задача", encoding="utf-8")
    remote.resume(where)
    assert [f.name for f in remote.waiting(where)] == ["a.again.txt"]
    assert "два пъти" in (where / "b.again.answer.txt").read_text(encoding="utf-8")
    assert not list(where.glob("*.working.txt"))


def test_the_apps_own_hub_gets_auto_update_and_the_remote_folder(settings, ctx, registry, monkeypatch):
    # `jarvis app` serves its hub before start_background: the background work must still start for it.
    from jarvis import app, remote

    hub, _ = make_hub(settings, ctx, registry, [])
    hub.restartable = True
    started = []
    monkeypatch.setattr(hub, "auto_update", lambda: started.append("update"))
    monkeypatch.setattr(remote, "watch", lambda h, home: started.append(("remote", h is hub)))
    monkeypatch.setattr(ctx.scheduler, "start", lambda: None)
    app.start_background(ctx)
    assert started == [("remote", True), "update"]


def test_a_remote_task_can_pick_the_brain_for_itself(settings, ctx, registry, tmp_path, monkeypatch):
    from jarvis import brain, remote

    hub, _ = make_hub(settings, ctx, registry, [])
    hub.accounts.create("owner@example.com", "Анастас", "123456", role="owner")
    seen = []
    monkeypatch.setattr(hub, "ask", lambda text, user, chat: seen.append((text, brain.CHOSEN.get())) or "Готово.")
    where = remote.folder(tmp_path)
    where.mkdir()
    (where / "groq.txt").write_text("модел: groq\nвиж имейлите", encoding="utf-8")
    remote.run_one(hub, where / "groq.txt")
    assert seen == [("виж имейлите", "groq")] and brain.CHOSEN.get() is None
    assert "Модел: groq" in (where / "groq.answer.txt").read_text(encoding="utf-8")


def test_voice_tab_lists_voices_and_plays_the_picked_one(settings, ctx, registry, monkeypatch):
    from jarvis import tts

    used = []
    monkeypatch.setattr(tts, "edge", lambda text, voice: used.append(voice) or b"mp3")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    hub, port = make_hub(settings, ctx, registry, [])
    voices = json.loads(call(port, "/api/voices")[1])
    assert voices["current"] == settings.tts_voice and any(v["id"] == "bg-BG-KalinaNeural" for v in voices["voices"])
    status, _audio = call(port, "/api/tts", {"text": voices["sample"], "voice": "en-US-AndrewMultilingualNeural"})
    assert status == 200 and used == ["en-US-AndrewMultilingualNeural"]
    try:
        call(port, "/api/tts", {"text": "здравей", "voice": "<script>"})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as err:
        assert err.code == 400


def test_elevenlabs_can_be_heard_in_the_voice_tab(settings, ctx, registry, monkeypatch):
    from jarvis import tts

    monkeypatch.setattr(tts, "elevenlabs", lambda text: b"eleven-mp3")
    hub, port = make_hub(settings, ctx, registry, [])
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    try:
        call(port, "/api/tts", {"text": "Здравей", "voice": "elevenlabs"})
        raise AssertionError("expected 400 without a key")
    except urllib.error.HTTPError as err:
        assert err.code == 400
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    assert call(port, "/api/tts", {"text": "Здравей", "voice": "elevenlabs"}) == (200, "eleven-mp3")
    assert json.loads(call(port, "/api/voices")[1])["eleven"] is True
    assert next(s for s in hub.settings_view() if s["title"] == "Глас")["ready"]


def test_settings_are_split_into_tidy_tabs():
    from jarvis.hub import EXTRA_SETTINGS, settings_tab
    from jarvis.setup_wizard import STEPS

    tabs = {title: settings_tab(title, [k for k, _l in fields]) for title, _w, fields in STEPS + EXTRA_SETTINGS}
    assert tabs["Мозък"] == "brain" and not any(t.startswith("Мозъкът") for t in tabs)
    assert tabs["Поведение"] == tabs["Съобщения"] == tabs["Как да се обръща към теб"] == "general"
    assert tabs["Глас"] == "voice" and tabs["Telegram"] == tabs["Обаждания и SMS"] == "service"
