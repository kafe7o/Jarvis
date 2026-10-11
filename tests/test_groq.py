from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from google.genai import errors

from conftest import Approver, FakeClient, response, text_block
from jarvis import gemini, groq, local, usage
from jarvis.brain import Jarvis
from jarvis.gemini import Block


class FakeGroq:
    """Stands in for Groq's API: answers in turn; an int answer is that HTTP error."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(json.loads(json.dumps(body)))
        answer = self.answers.pop(0)
        if isinstance(answer, tuple):
            code, detail = answer
            raise urllib.error.HTTPError(groq.URL, code, "error", {}, io.BytesIO(detail.encode()))
        return answer


def said(text, tool_calls=None):
    return {"choices": [{"message": {"content": text, "tool_calls": tool_calls},
                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 40}}


def call(name, args, id="call_1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "free-key")


def test_groq_is_the_brain_with_the_useful_tools_and_costs_nothing(settings, ctx, registry, key):
    settings.model = "groq"
    fake = FakeGroq([said("", [call("remember", {"topic": "кафе", "fact": "без захар"})]), said("Запомних, сър.")])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), groq_brain=groq.GroqBrain(fake))
    assert jarvis.ask("запомни, че пия кафе без захар") == "Запомних, сър."
    first, second = fake.bodies
    assert first["model"] == "openai/gpt-oss-120b" and first["reasoning_effort"] == "medium"
    assert {t["function"]["name"] for t in first["tools"]} <= local.LOCAL_TOOLS and first["max_tokens"] <= 2048
    assert first["messages"][0]["role"] == "system" and first["messages"][-1]["role"] == "user"
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "remember"
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "Remembered (id 1)."}
    assert ctx.store.query("SELECT fact FROM facts")[0]["fact"] == "без захар"
    models = {r["model"] for r in ctx.store.query("SELECT model FROM usage")}
    assert models == {"groq:openai/gpt-oss-120b"} and usage.price("groq:openai/gpt-oss-120b") == (0, 0, 0, 0)


def test_a_busy_or_used_up_model_hands_over_to_the_next_free_one(key):
    rested = []
    fake = FakeGroq([(429, '{"error": {"message": "Rate limit reached ... tokens per day (TPD)"}}'),
                     (413, '{"error": {"message": "Request too large"}}'),
                     (429, '{"error": {"message": "Limit ... requests per minute (RPM)"}}'),
                     said("Тук съм.")])
    brain = groq.GroqBrain(fake, wait=rested.append)
    answer = brain.create(model="groq", system="Ти си Jarvis.", messages=[{"role": "user", "content": "ехо"}],
                          tools=[], max_tokens=4000, effort="low")
    assert answer.content[0].text == "Тук съм." and answer.model == "groq:openai/gpt-oss-20b"
    assert [b["model"] for b in fake.bodies] == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile",
                                                  "openai/gpt-oss-20b", "openai/gpt-oss-20b"]
    assert rested == [5.0] and "tools" not in fake.bodies[0] and fake.bodies[0]["max_tokens"] == 1500
    assert brain.models("groq") == ["openai/gpt-oss-20b", "llama-3.1-8b-instant"]  # the others rest for a while
    with pytest.raises(groq.UsedUp):
        groq.GroqBrain(FakeGroq([(503, "down")] * 4)).create(model="groq", system="", messages=[], tools=[],
                                                              max_tokens=100, effort="low")


def test_a_long_conversation_is_cut_to_fit_without_breaking_tool_calls():
    chat = groq.to_messages("system", [
        {"role": "user", "content": "старо " * 3000},
        {"role": "assistant", "content": [Block("tool_use", id="t1", name="recall", input={})]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "нищо"}]},
        {"role": "assistant", "content": [Block("text", text="Нищо не помня.")]},
        {"role": "user", "content": [{"type": "text", "text": "ново"}, {"type": "image", "source": {}}]},
    ])
    assert [m["role"] for m in chat] == ["system", "user", "assistant", "tool", "assistant", "user"]
    cut = groq.trim(chat, 2000)
    assert [m["role"] for m in cut] == ["system", "user"] and cut[-1]["content"].startswith("ново")
    assert "cannot see images" in cut[-1]["content"]


def test_groq_takes_the_quick_lane_and_stands_in_when_gemini_is_used_up(settings, ctx, registry, key, monkeypatch):
    settings.router, settings.model = True, gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    fake = FakeGroq([said("Столицата на Франция е Париж."), said("Запомних.")])
    daily = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                               "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
    google = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: (_ for _ in ()).throw(daily)))
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), groq_brain=groq.GroqBrain(fake),
                    gemini_brain=gemini.GeminiBrain(google, wait=lambda s: None))
    assert jarvis.fast_model() == "groq"
    assert jarvis.ask("коя е столицата на Франция?") == "Столицата на Франция е Париж."
    assert "tools" not in fake.bodies[0] and fake.bodies[0]["reasoning_effort"] == "low"
    assert jarvis.ask("запомни, че тренирам в 7") == "Запомних."  # the full agent: Gemini is used up, Groq answers
    assert "tools" in fake.bodies[1]


def test_switching_to_groq_needs_its_key(settings, ctx, registry, monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL", settings.model)
    out, err = registry.run("switch_brain", {"brain": "groq"}, Approver())
    assert err and "console.groq.com" in out
    monkeypatch.setenv("GROQ_API_KEY", "free-key")
    out, err = registry.run("switch_brain", {"brain": "groq"}, Approver())
    assert not err and settings.model == "groq"


def test_claude_without_credit_hands_over_to_groq(settings, ctx, registry, key, monkeypatch):
    import anthropic

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings.model = "claude-opus-5-5"

    class NoCredit:
        def __init__(self):
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            raise anthropic.BadRequestError("Your credit balance is too low", response=SimpleNamespace(
                status_code=400, headers={}, request=None), body=None)

    fake = FakeGroq([said("Тук съм, през Groq.")])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=NoCredit(), groq_brain=groq.GroqBrain(fake))
    assert jarvis.ask("здрасти") == "Тук съм, през Groq." and settings.model == "groq"
