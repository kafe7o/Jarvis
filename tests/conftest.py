from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis import local
from jarvis.config import Settings
from jarvis.plugins import Context, load_all
from jarvis.store import Store
from jarvis.tools import ToolRegistry


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args, id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id)


def system_text(request):
    """The system prompt of a recorded request as one string."""
    system = request["system"]
    return system if isinstance(system, str) else "\n".join(block["text"] for block in system)


def response(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


class FakeClient:
    """Stands in for anthropic.Anthropic; returns scripted responses and records requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for var in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "STRIPE_API_KEY", "JARVIS_PUBLIC_URL",
                "GROQ_API_KEY", "JARVIS_GROQ_MODEL", "GEMINI_API_KEY", "JARVIS_MODEL", "JARVIS_AUTO_REPLY", "JARVIS_REPLY_STYLE", "JARVIS_SILENCE"]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("JARVIS_PHONE_NEURAL", "0")
    s = Settings()
    s.home = tmp_path / "home"
    s.allowed_roots = [str(tmp_path)]
    s.trust_local_actions = False
    s.twilio_sid = s.twilio_token = s.twilio_number = s.stripe_key = None
    s.owner_phone = "+359888000000"
    s.router = False  # most tests drive the full agent; test_router.py covers the cheap levels
    s.act_check = False  # scripted answers; test_core.py covers the second look at answers without action
    s.vault = tmp_path / "vault"
    monkeypatch.setattr(local, "_seen", {"at": float("inf"), "models": []})  # no Ollama unless a test adds one
    return s


@pytest.fixture
def ctx(settings):
    return Context(settings=settings, store=Store(settings.db_path), notifiers=[])


@pytest.fixture
def registry(ctx):
    reg = ToolRegistry()
    load_all(reg, ctx)
    return reg


class Approver:
    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []

    def __call__(self, summary):
        self.asked.append(summary)
        return self.answer
