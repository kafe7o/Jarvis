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
    status, data = web("/api/setup", {"name": "Анастас", "username": "kafe7o", "password": "секрет1"})
    assert status == 200 and data["user"]["role"] == "owner"
    assert web("/api/me")[1]["user"]["name"] == "Анастас"
    # nobody can become owner a second time
    assert web("/api/setup", {"name": "x", "username": "x", "password": "123456"})[0] == 409
    web("/api/logout", {})
    assert web("/api/chats")[0] == 401
    assert web("/api/login", {"username": "kafe7o", "password": "грешна"})[0] == 401
    assert web("/api/login", {"username": "KAFE7O", "password": "секрет1"})[0] == 200
    assert web("/api/chats")[0] == 200


def test_posts_need_the_app_header(settings, ctx, registry):
    hub, web, _ = make(settings, ctx, registry, [])
    web("/api/setup", {"name": "A", "username": "owner", "password": "123456"})
    status, _ = web("/api/chats", {}, headers={"X-Jarvis": "0"})
    assert status == 403


def test_chat_flow_and_titles(settings, ctx, registry):
    hub, web, client = make(settings, ctx, registry, [response(text_block("Здравейте, сър."))])
    web("/api/setup", {"name": "A", "username": "owner", "password": "123456"})
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
    owner("/api/setup", {"name": "Анастас", "username": "owner", "password": "123456"})
    status, data = owner("/api/users", {"name": "Мария", "username": "maria", "password": "123456"})
    maria_id = data["user"]["id"]
    assert data["user"]["perms"]["web"] == "on" and data["user"]["perms"]["system"] == "off"

    maria = Browser(owner.port)
    assert maria("/api/login", {"username": "maria", "password": "123456"})[0] == 200
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
    owner("/api/setup", {"name": "A", "username": "owner", "password": "123456"})
    owner("/api/users", {"name": "Мария", "username": "maria", "password": "123456"})
    maria = Browser(owner.port)
    maria("/api/login", {"username": "maria", "password": "123456"})
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
    owner("/api/setup", {"name": "A", "username": "owner", "password": "123456"})
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
    web("/api/setup", {"name": "A", "username": "owner", "password": "123456"})
    chat = web("/api/chats", {})[1]["chat"]
    data = web(f"/api/chats/{chat['id']}/ask", {"text": "здрасти"})[1]
    assert data["error"] and "Claude ключът" in data["answer"]


def test_last_owner_cannot_be_removed(ctx):
    accounts = Accounts(ctx.store)
    owner = accounts.create("owner", "O", "123456", role="owner")
    try:
        accounts.delete(owner.id)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
