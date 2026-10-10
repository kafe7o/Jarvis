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
    usage = json.loads(call(port, "/api/usage")[1])
    assert usage["today"] == {"cost": 0, "requests": 0} and len(usage["days"]) == 14


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
