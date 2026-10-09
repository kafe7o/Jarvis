from __future__ import annotations

import io
import json
import urllib.error
from types import SimpleNamespace

import pytest
from google.genai import errors

from conftest import Approver, FakeClient
from jarvis import freeapi, gemini, usage
from jarvis.brain import Jarvis
from jarvis.config import default_brain

NVIDIA_LIST = [{"id": i} for i in [
    "meta/llama-3.3-70b-instruct", "nvidia/nv-embedqa-e5-v5", "moonshotai/kimi-k2-instruct",
    "moonshotai/kimi-k2-instruct-0905", "moonshotai/kimi-k2-thinking", "deepseek-ai/deepseek-v3.1",
    "deepseek-ai/deepseek-v3.2", "meta/llama-guard-4-12b", "qwen/qwen3-coder-480b-a35b-instruct",
    "meta/llama-3.2-90b-vision-instruct", "google/paligemma", "openai/gpt-oss-120b", "some/unknown-7b"]]
OPENROUTER_LIST = [
    {"id": "openai/gpt-5", "pricing": {"prompt": "0.000001", "completion": "0.00001"}, "supported_parameters": ["tools"]},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": ["tools", "tool_choice"]},
    {"id": "deepseek/deepseek-chat-v3.1:free", "pricing": {"prompt": "0", "completion": "0"},
     "supported_parameters": ["tools"]},
    {"id": "google/gemma-3-27b-it:free", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": []},
    {"id": "tiny/helper:free", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["tools"]},
]


class FakeAPI:
    """Stands in for an OpenAI-style API: answers in turn; a tuple answer is that HTTP error."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(json.loads(json.dumps(body)))
        answer = self.answers.pop(0)
        if isinstance(answer, tuple):
            code, detail = answer
            raise urllib.error.HTTPError("https://x", code, "error", {}, io.BytesIO(detail.encode()))
        return answer


def said(text, tool_calls=None):
    return {"choices": [{"message": {"content": text, "tool_calls": tool_calls},
                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 9000, "completion_tokens": 40}}


def call(name, args, id="call_1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


@pytest.fixture
def nvidia_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "free-key")


def test_the_strongest_models_that_can_use_tools_go_first():
    nvidia = freeapi.choose(freeapi.PROVIDERS["nvidia"], NVIDIA_LIST)
    assert nvidia == ["moonshotai/kimi-k2-instruct-0905", "moonshotai/kimi-k2-instruct", "deepseek-ai/deepseek-v3.2",
                      "deepseek-ai/deepseek-v3.1", "openai/gpt-oss-120b", "meta/llama-3.3-70b-instruct",
                      "moonshotai/kimi-k2-thinking"]  # no search, safety, picture or coder models, no unknown ones
    openrouter = freeapi.choose(freeapi.PROVIDERS["openrouter"], OPENROUTER_LIST)
    assert openrouter == ["deepseek/deepseek-chat-v3.1:free", "meta-llama/llama-3.3-70b-instruct:free",
                          "tiny/helper:free"]  # only free ones that can use tools; paid gpt-5 is left out


def test_nvidia_is_the_brain_with_every_tool_and_costs_nothing(settings, ctx, registry, nvidia_key):
    settings.model = "nvidia"
    fake = FakeAPI([said("", [call("remember", {"topic": "кафе", "fact": "без захар"})]), said("Запомних, сър.")])
    brain = freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), free_brains={"nvidia": brain})
    assert jarvis.ask("запомни, че пия кафе без захар") == "Запомних, сър."
    first, second = fake.bodies
    assert first["model"] == "moonshotai/kimi-k2-instruct-0905" and first["tool_choice"] == "auto"
    assert len(first["tools"]) == len(registry.definitions(None))  # every tool, not only Groq's short list
    assert second["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "Remembered (id 1)."}
    assert ctx.store.query("SELECT fact FROM facts")[0]["fact"] == "без захар"
    models = {r["model"] for r in ctx.store.query("SELECT model FROM usage")}
    assert models == {"nvidia:moonshotai/kimi-k2-instruct-0905"}
    assert usage.price("nvidia:moonshotai/kimi-k2-instruct-0905") == (0, 0, 0, 0)


def test_a_busy_missing_or_slow_model_hands_over_to_the_next_one(nvidia_key):
    rested = []
    fake = FakeAPI([(429, '{"status": 429, "title": "Too Many Requests"}'), (429, "Too Many Requests"),
                    (404, "Function not found for account"), said("Тук съм.")])
    brain = freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST, wait=rested.append)
    answer = brain.create(model="nvidia", system="Ти си Jarvis.", messages=[{"role": "user", "content": "ехо"}],
                          tools=[], max_tokens=8000, effort="low")
    assert answer.content[0].text == "Тук съм." and answer.model == "nvidia:deepseek-ai/deepseek-v3.2"
    assert [b["model"] for b in fake.bodies] == ["moonshotai/kimi-k2-instruct-0905", "moonshotai/kimi-k2-instruct-0905",
                                                  "moonshotai/kimi-k2-instruct", "deepseek-ai/deepseek-v3.2"]
    assert rested == [5.0] and "tools" not in fake.bodies[0] and fake.bodies[0]["max_tokens"] == 4096
    assert brain.models("nvidia")[0] == "deepseek-ai/deepseek-v3.2"  # the other two rest for a while
    with pytest.raises(freeapi.UsedUp):
        freeapi.FreeBrain("nvidia", post=FakeAPI([(503, "down")] * 3), get=lambda: NVIDIA_LIST).create(
            model="nvidia", system="", messages=[], tools=[], max_tokens=100, effort="low")


def test_openrouter_daily_limit_rests_it_until_tomorrow_and_its_list_falls_back(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-key")
    fake = FakeAPI([(429, '{"error": {"message": "Rate limit exceeded: free-models-per-day"}}')])

    def offline():
        raise urllib.error.URLError("no internet")

    brain = freeapi.FreeBrain("openrouter", post=fake, get=offline)
    assert brain.listing() == list(freeapi.PROVIDERS["openrouter"].fallback)
    with pytest.raises(freeapi.UsedUp, match="today"):
        brain.create(model="openrouter", system="", messages=[], tools=[], max_tokens=100, effort="low")
    assert brain.models("openrouter") == []  # no more requests to it today
    assert len(fake.bodies) == 1


def test_a_wrong_key_says_where_to_get_a_new_one(monkeypatch):
    from jarvis.hub import friendly_error

    monkeypatch.setenv("OPENROUTER_API_KEY", "wrong")
    brain = freeapi.FreeBrain("openrouter", post=FakeAPI([(401, "No auth credentials found")]), get=lambda: [])
    with pytest.raises(RuntimeError) as err:
        brain.create(model="openrouter:deepseek/deepseek-chat-v3.1:free", system="", messages=[], tools=[],
                     max_tokens=100, effort="low")
    assert "openrouter.ai/keys" in friendly_error(err.value)


def test_nvidia_stands_in_when_gemini_is_used_up_and_groq_is_not_set(settings, ctx, registry, nvidia_key, monkeypatch):
    settings.model = gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    daily = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                               "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
    google = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: (_ for _ in ()).throw(daily)))
    fake = FakeAPI([said("Тук съм, през NVIDIA.")])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]),
                    gemini_brain=gemini.GeminiBrain(google, wait=lambda s: None),
                    free_brains={"nvidia": freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)})
    assert jarvis.ask("здрасти") == "Тук съм, през NVIDIA." and settings.model == gemini.DEFAULT_MODEL


def test_without_gemini_or_groq_a_free_key_still_makes_jarvis_free(settings, ctx, registry, monkeypatch):
    assert default_brain() == "claude-opus-5-5"
    monkeypatch.setenv("OPENROUTER_API_KEY", "free-key")
    assert default_brain() == "openrouter"
    monkeypatch.setenv("NVIDIA_API_KEY", "free-key")
    assert default_brain() == "nvidia"
    monkeypatch.setenv("JARVIS_MODEL", settings.model)
    out, err = registry.run("switch_brain", {"brain": "openrouter"}, Approver())
    assert not err and settings.model == "openrouter"
    monkeypatch.delenv("NVIDIA_API_KEY")
    out, err = registry.run("switch_brain", {"brain": "nvidia"}, Approver())
    assert err and "build.nvidia.com" in out
