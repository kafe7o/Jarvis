from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from conftest import Approver, FakeClient, response, text_block, tool_block

from jarvis.accounts import Accounts, User, check_password, hash_password
from jarvis.brain import Jarvis
from jarvis.hub import Hub


class Browser:
    """A tiny cookie-keeping client, like the app in a browser."""

    def __init__(self, port):
        self.port = port
        self.cookie = ""

    def __call__(self, path, body=None, headers=None):
        hdrs = {"X-Jarvis": "1", "Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            hdrs["Cookie"] = self.cookie
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers=hdrs, method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                set_cookie = resp.headers.get("Set-Cookie")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")


def make(settings, ctx, registry, responses):
    client = FakeClient(responses)
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(False), client=client)
    hub = Hub(ctx, "secret", port=0, host="127.0.0.1")
    server = hub.serve()
    return hub, Browser(server.server_address[1]), client


def test_passwords_are_hashed():
    stored = hash_password("тайна123")
    assert "тайна" not in stored and check_password("тайна123", stored) and not check_password("друга", stored)


def test_first_run_creates_owner_then_login(settings, ctx, registry):
    hub, web, _ = make(settings, ctx, registry, [])
    status, me = web("/api/me")
    assert me["user"] is None and me["needs_setup"] and me["can_setup"]
    status, data = web("/api/setup", {"name": "Анастас", "email": "owner@example.com", "password": "секрет1"})
    assert status == 200 and data["user"]["role"] == "owner"
    assert web("/api/me")[1]["user"]["name"] == "Анастас"
    # nobody can become owner a second time
    assert web("/api/setup", {"name": "x", "email": "x@example.com", "password": "123456"})[0] == 409
    web("/api/logout", {})
    assert web("/api/chats")[0] == 401
    assert web("/api/login", {"email": "owner@example.com", "password": "грешна"})[0] == 401
    assert web("/api/login", {"email": "OWNER@example.com", "password": "секрет1"})[0] == 200
    assert web("/api/chats")[0] == 200


def test_posts_need_the_app_header(settings, ctx, registry):
    hub, web, _ = make(settings, ctx, registry, [])
    web("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    status, _ = web("/api/chats", {}, headers={"X-Jarvis": "0"})
    assert status == 403


def test_chat_flow_and_titles(settings, ctx, registry):
    hub, web, client = make(settings, ctx, registry, [response(text_block("Здравейте, сър."))])
    web("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    chat = web("/api/chats", {})[1]["chat"]
    status, data = web(f"/api/chats/{chat['id']}/ask", {"text": "Здрасти, Джарвис"})
    assert data["answer"] == "Здравейте, сър." and data["chat"]["title"] == "Здрасти, Джарвис"
    messages = web(f"/api/chats/{chat['id']}")[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert web(f"/api/chats/{chat['id']}/delete", {})[0] == 200
    assert web("/api/chats")[1]["chats"] == []


def test_member_permissions_limit_tools_and_privacy(settings, ctx, registry):
    hub, owner, client = make(settings, ctx, registry, [
        response(text_block("Здравей, Мария.")),
        response(tool_block("run_shell", {"command": "echo hi"}), stop="tool_use"),
        response(text_block("Не мога.")),
    ])
    ctx.store.insert("facts", topic="owner", fact="Тайният код на сейфа е 1234")
    owner("/api/setup", {"name": "Анастас", "email": "owner@example.com", "password": "123456"})
    status, data = owner("/api/users", {"name": "Мария", "email": "maria@example.com", "password": "123456"})
    maria_id = data["user"]["id"]
    assert data["user"]["perms"]["web"] == "on" and data["user"]["perms"]["system"] == "off"

    maria = Browser(owner.port)
    assert maria("/api/login", {"email": "maria@example.com", "password": "123456"})[0] == 200
    assert maria("/api/users")[0] == 403  # members cannot manage accounts
    chat = maria("/api/chats", {})[1]["chat"]
    maria(f"/api/chats/{chat['id']}/ask", {"text": "Здрасти"})
    request = client.requests[-1]
    names = {t["name"] for t in request["tools"]}
    assert "web_search" in names and "request_approval" in names
    assert "run_shell" not in names and "remember" not in names
    assert "1234" not in request["system"] and "Мария" in request["system"]

    # even if the model asks for a disabled tool, it does not run
    maria(f"/api/chats/{chat['id']}/ask", {"text": "пусни команда"})
    result = client.requests[-1]["messages"][-1]["content"][0]
    assert result["is_error"] and "not allowed" in result["content"]

    # the owner's chats are not visible to the member
    owner_chat = owner("/api/chats", {})[1]["chat"]
    assert maria(f"/api/chats/{owner_chat['id']}")[0] == 404

    # the owner can switch an ability on
    status, data = owner(f"/api/users/{maria_id}", {"perms": {"system": "ask"}})
    assert data["user"]["perms"]["system"] == "ask"


def test_ask_mode_confirms_every_action(settings, ctx, registry, tmp_path):
    target = tmp_path / "x.txt"
    target.write_text("здрасти")
    owner = User(1, "o", "O", "owner", {"system": "ask"})
    assert "system" in owner.ask_groups()
    approver = Approver(False)
    out, err = registry.run("read_file", {"path": str(target)}, approver, allowed=owner.allowed_groups(),
                            ask_groups=owner.ask_groups())
    assert err and approver.asked  # read_file normally runs without asking


def test_confirmations_only_reach_the_right_account(settings, ctx, registry, tmp_path):
    target = tmp_path / "note.txt"
    hub, owner, client = make(settings, ctx, registry, [
        response(tool_block("write_file", {"path": str(target), "content": "да"}), stop="tool_use"),
        response(text_block("Готово.")),
    ])
    owner("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    owner("/api/users", {"name": "Мария", "email": "maria@example.com", "password": "123456"})
    maria = Browser(owner.port)
    maria("/api/login", {"email": "maria@example.com", "password": "123456"})
    chat = owner("/api/chats", {})[1]["chat"]
    answers = {}
    t = threading.Thread(target=lambda: answers.update(owner(f"/api/chats/{chat['id']}/ask", {"text": "запиши"})[1]))
    t.start()
    events = []
    for _ in range(50):
        events = [e for e in hub.events.events if e["kind"] == "confirm"]
        if events:
            break
        threading.Event().wait(0.1)
    confirm = events[0]
    assert maria("/api/confirm", {"id": confirm["id"], "yes": True})[1]["ok"] is False
    assert maria("/api/events?after=0")[1]["events"] == []
    assert owner("/api/confirm", {"id": confirm["id"], "yes": True})[1]["ok"] is True
    t.join(10)
    assert answers["answer"] == "Готово." and target.read_text() == "да"


def test_connections_settings_mask_secrets(settings, ctx, registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-123456\nJARVIS_USER_NAME=сър\n", encoding="utf-8")
    hub, owner, _ = make(settings, ctx, registry, [])
    owner("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    sections = owner("/api/settings")[1]["sections"]
    brain = sections[0]["fields"][0]
    assert brain["set"] and brain["value"] == "" and "sk-ant-123456" not in json.dumps(sections)
    owner("/api/settings", {"values": {"STRIPE_API_KEY": "sk_test_1", "BOGUS": "x"}})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "STRIPE_API_KEY=sk_test_1" in text and "ANTHROPIC_API_KEY=sk-ant-123456" in text and "BOGUS" not in text


def test_friendly_error_when_claude_key_is_missing(settings, ctx, registry):
    class Broken:
        def __init__(self):
            self.beta = self
            self.messages = self

        def create(self, **_kw):
            raise RuntimeError("Could not resolve authentication method. Expected either api_key or auth_token")

    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(False), client=Broken())
    hub = Hub(ctx, "secret", port=0, host="127.0.0.1")
    web = Browser(hub.serve().server_address[1])
    web("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    chat = web("/api/chats", {})[1]["chat"]
    data = web(f"/api/chats/{chat['id']}/ask", {"text": "здрасти"})[1]
    assert data["error"] and "Claude ключът" in data["answer"]


def test_last_owner_cannot_be_removed(ctx):
    accounts = Accounts(ctx.store)
    owner = accounts.create("owner@example.com", "O", "123456", role="owner")
    try:
        accounts.delete(owner.id)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_owner_manages_logins_members_cannot(settings, ctx, registry):
    hub, owner, _ = make(settings, ctx, registry, [])
    owner("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    assert owner("/api/users", {"name": "Иван", "email": "не-е-имейл", "password": "123456"})[0] == 400
    uid = owner("/api/users", {"name": "Иван", "email": "ivan@example.com", "password": "123456"})[1]["user"]["id"]
    ivan = Browser(owner.port)
    ivan("/api/login", {"email": "ivan@example.com", "password": "123456"})
    assert ivan("/api/me", {"old_password": "123456", "password": "новапарола"})[0] == 403
    assert ivan("/api/me", {"name": "Ванко"})[1]["user"]["name"] == "Ванко"
    owner(f"/api/users/{uid}", {"email": "vanko@example.com", "password": "друга123"})
    assert Browser(owner.port)("/api/login", {"email": "vanko@example.com", "password": "друга123"})[0] == 200
    # owner changes own e-mail only with the current password
    assert owner("/api/me", {"old_password": "грешна", "email": "new@example.com"})[0] == 400
    assert owner("/api/me", {"old_password": "123456", "email": "new@example.com"})[1]["user"]["email"] == "new@example.com"


def test_set_owner_creates_then_resets(ctx):
    accounts = Accounts(ctx.store)
    accounts.set_owner("a@example.com", "123456", "А")
    accounts.set_owner("b@example.com", "654321")
    assert [u.username for u in accounts.list()] == ["b@example.com"]
    assert accounts.login("b@example.com", "654321") and not accounts.login("a@example.com", "123456")


def test_saved_settings_apply_without_restart(settings, ctx, registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_EFFORT", "xhigh")  # restored after the test
    (tmp_path / ".env").write_text("JARVIS_EFFORT=xhigh\n", encoding="utf-8")
    hub, owner, _ = make(settings, ctx, registry, [])
    owner("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    db_path = ctx.settings.db_path
    owner("/api/settings", {"values": {"JARVIS_EFFORT": "low"}})
    assert ctx.settings.effort == "low" and ctx.jarvis.settings.effort == "low"
    assert ctx.settings.db_path == db_path  # untouched fields stay as they were


def wait_for_confirms(hub, n=1):
    for _ in range(100):
        events = [e for e in hub.events.events if e["kind"] == "confirm"]
        if len(events) >= n:
            return events
        threading.Event().wait(0.05)
    return [e for e in hub.events.events if e["kind"] == "confirm"]


def test_always_allow_stops_asking_for_computer_actions(settings, ctx, registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_TRUST_LOCAL", "0")  # restored after the test
    (tmp_path / ".env").write_text("JARVIS_TRUST_LOCAL=0\n", encoding="utf-8")
    hub, owner, client = make(settings, ctx, registry, [
        response(tool_block("run_shell", {"command": "echo one"}), stop="tool_use"),
        response(tool_block("run_shell", {"command": "echo two"}), stop="tool_use"),
        response(text_block("Готово.")),
    ])
    owner("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    chat = owner("/api/chats", {})[1]["chat"]
    answers = {}
    t = threading.Thread(target=lambda: answers.update(owner(f"/api/chats/{chat['id']}/ask", {"text": "пусни"})[1]))
    t.start()
    first = wait_for_confirms(hub)[0]
    assert first["always"] is True
    assert owner("/api/confirm", {"id": first["id"], "yes": True, "always": True})[1]["ok"] is True
    t.join(15)
    assert answers["answer"] == "Готово."
    assert len([e for e in hub.events.events if e["kind"] == "confirm"]) == 1  # the second command ran without asking
    assert ctx.settings.trust_local_actions is True
    assert "JARVIS_TRUST_LOCAL=1" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_always_allow_is_never_offered_for_calls(settings, ctx, registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_TRUST_LOCAL", "0")
    hub, owner, client = make(settings, ctx, registry, [
        response(tool_block("request_approval", {"action": "Обади се на Краси"}), stop="tool_use"),
        response(text_block("Добре.")),
    ])
    owner("/api/setup", {"name": "A", "email": "owner@example.com", "password": "123456"})
    chat = owner("/api/chats", {})[1]["chat"]
    t = threading.Thread(target=lambda: owner(f"/api/chats/{chat['id']}/ask", {"text": "звънни"}))
    t.start()
    confirm = wait_for_confirms(hub)[0]
    assert confirm["always"] is False
    owner("/api/confirm", {"id": confirm["id"], "yes": True, "always": True})
    t.join(15)
    assert ctx.settings.trust_local_actions is False
