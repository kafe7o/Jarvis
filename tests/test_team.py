from __future__ import annotations

import threading
from types import SimpleNamespace

from conftest import Approver, response, text_block, tool_block

from jarvis import core_files
from jarvis.accounts import User
from jarvis.brain import Jarvis


class TeamClient:
    """Answers by who is asking (the system prompt), so parallel specialists get their own script."""

    def __init__(self, scripts):
        self.scripts = {who: list(r) for who, r in scripts.items()}
        self.requests = []
        self.lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        with self.lock:
            self.requests.append(kwargs)
            who = next((w for w in self.scripts if w != "Jarvis" and w in kwargs["system"]), "Jarvis")
            return self.scripts[who].pop(0)


def tools_for(client, who):
    return [{t["name"] for t in r["tools"]} for r in client.requests if who in r["system"]]


def test_jarvis_hands_work_to_specialists_in_parallel(settings, ctx, registry, tmp_path):
    target = tmp_path / "plan.txt"
    client = TeamClient({
        "Jarvis": [
            response(tool_block("delegate", {"assignments": [
                {"agent": "researcher", "task": "Намери 3 идеи за видео"},
                {"agent": "developer", "task": f"Запиши плана в {target}"},
            ]}), stop="tool_use"),
            response(text_block("Екипът свърши работата, сър.")),
        ],
        "Изследователят": [response(text_block("Три идеи: А, Б, В."))],
        "Програмистът": [
            response(tool_block("write_file", {"path": str(target), "content": "план"}, id="tu_9"), stop="tool_use"),
            response(text_block("Записах плана.")),
        ],
    })
    approver = Approver(True)
    jarvis = Jarvis(settings, ctx.store, registry, approver, client=client)
    ctx.jarvis = jarvis
    owner = User(1, "owner@example.com", "Анастас", "owner")
    assert jarvis.ask("Направи план за видео", user=owner) == "Екипът свърши работата, сър."
    report = client.requests[-1]["messages"][-1]["content"][0]["content"]
    assert "Три идеи" in report and "Записах плана" in report
    assert target.read_text() == "план" and any("plan.txt" in a for a in approver.asked)  # the owner still approved
    researcher = tools_for(client, "Изследователят")[0]
    assert "run_shell" not in researcher and "delegate" not in researcher and "recall" in researcher
    assert "delegate" not in tools_for(client, "Програмистът")[0]
    agents = {r["agent"] for r in ctx.store.query("SELECT agent FROM activity")}
    assert {"Jarvis", "Програмистът"} <= agents


def test_specialists_never_get_more_than_the_account(settings, ctx, registry):
    client = TeamClient({
        "Jarvis": [
            response(tool_block("delegate", {"assignments": [{"agent": "developer", "task": "пусни команда"}]}),
                     stop="tool_use"),
            response(text_block("Готово.")),
        ],
        "Програмистът": [response(text_block("Нямам достъп до компютъра."))],
    })
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=client)
    ctx.jarvis = jarvis
    member = User(2, "m@example.com", "Мария", "member", {"team": "on"})
    jarvis.ask("направи нещо", user=member)
    developer = tools_for(client, "Програмистът")[0]
    assert "run_shell" not in developer and "write_file" not in developer


def test_core_files_go_into_the_prompt(settings, ctx, registry):
    core_files.write(settings.home, "SOUL.md", "Винаги започвай с „На вашите услуги“.")
    core_files.write(settings.home, "USER.md", "Казвам се Анастас.")
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True), client=None or SimpleNamespace())
    owner = User(1, "o@example.com", "А", "owner")
    guest = User(2, "g@example.com", "Г", "member")
    assert "На вашите услуги" in jarvis.system_prompt(owner) and "Анастас" in jarvis.system_prompt(owner)
    assert "На вашите услуги" in jarvis.system_prompt(guest) and "Анастас" not in jarvis.system_prompt(guest)
