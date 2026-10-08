"""The three levels (router.py), the daily cap, the brain on this computer and the notes vault."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from jarvis import gemini, local, router, usage
from jarvis.brain import BudgetReached, Jarvis
from jarvis.routines import ROUTINES

from conftest import Approver, FakeClient, response, text_block, tool_block

NOW = datetime(2026, 10, 8, 15, 0)


def info(**kw):
    return router.Info(user_name="Анастас", now=lambda: NOW, devices=lambda: {"phone": "serial"}, **kw)


def steps(text, **kw):
    found = router.match(text, info(**kw))
    return [(s.tool, s.args) for s in found] if found else None


@pytest.mark.parametrize("text, tool, args", [
    ("пусни Back in Black", "play_youtube", {"query": "Back in Black"}),
    ("pusni AC/DC", "play_youtube", {"query": "AC/DC"}),
    ("Джарвис, пусни лофи музика, моля", "play_youtube", {"query": "лофи музика"}),
    ("пусни Eminem на телефона", "play_youtube", {"query": "Eminem", "device": "phone"}),
    ("спри музиката", "media_control", {"key": "play_pause", "times": 1}),
    ("следваща песен", "media_control", {"key": "next", "times": 1}),
    ("po-silno", "media_control", {"key": "volume_up", "times": 5}),
    ("звука на 40", "set_volume", {"level": 40}),
    ("отвори youtube", "open_target", {"target": "https://www.youtube.com"}),
    ("otvori kalkulatora", "open_target", {"target": "calc"}),
    ("отвори abv.bg", "open_target", {"target": "https://www.abv.bg"}),
    ("отвори tiktok на телефона", "android", {"device": "phone", "action": "open_app", "name": "tiktok"}),
    ("заключи компютъра", "lock_computer", {"action": "lock"}),
    ("zakluchi kompyutara", "lock_computer", {"action": "lock"}),
    ("изключи компютъра", "power_off", {"action": "shutdown"}),
    ("какво ще е времето утре в Пловдив", "weather", {"place": "Пловдив", "days": 3}),
    ("vremeto v plovdiv", "weather", {"place": "plovdiv", "days": 3}),
    ("напомни ми след 10 минути да изключа фурната", "add_reminder",
     {"text": "да изключа фурната", "at": "2026-10-08T15:10:00"}),
    ("напомни ми в 6 да се обадя на мама", "add_reminder", {"text": "да се обадя на мама", "at": "2026-10-08T18:00:00"}),
    ("напомни ми утре да платя тока в 10", "add_reminder", {"text": "да платя тока", "at": "2026-10-09T10:00:00"}),
    ("napomni mi sled chas da izlqza", "add_reminder", {"text": "da izlqza", "at": "2026-10-08T16:00:00"}),
])
def test_commands_are_found_in_cyrillic_and_latin_letters(text, tool, args):
    assert steps(text) == [(tool, args)]


@pytest.mark.parametrize("text", [
    "пусни таймер за 5 минути", "включи лампата", "пусни Eminem на телевизора", "отвори photoshop", "продължи",
    "изпрати SMS на мама", "какво е черна дупка", "напомни ми да купя хляб", "пусни jarvis режим",
])
def test_anything_else_is_left_to_the_ai(text):
    assert steps(text) is None


def test_two_commands_in_one_sentence_but_not_a_band_name():
    assert [t for t, _ in steps("пусни Back in Black и заключи компютъра")] == ["play_youtube", "lock_computer"]
    assert steps("пусни AC/DC и Metallica") == [("play_youtube", {"query": "AC/DC и Metallica"})]


def test_talk_without_ai():
    say = lambda text, **kw: router.match(text, info(**kw))[0].say("")  # noqa: E731
    assert say("колко е часът") == "Часът е 15:00."
    assert say("koi den e dnes") == "Днес е четвъртък, 8 октомври 2026 г."
    assert say("здрасти") == "Добър ден, Анастас. С какво да помогна?"
    assert say("добро утро", weather_now=lambda: "Навън е 12 градуса.") == "Добро утро, Анастас. Часът е 15:00. Навън е 12 градуса."
    assert "Анастас" in say("благодаря")
    assert router.match("благодаря", info(last_answer="Да го изпратя ли?")) is None  # that is an answer, not thanks
    assert router.match("колко похарчих днес", info(owner=False)) is None


def test_which_questions_go_to_the_quick_model():
    assert router.quick("какво е черна дупка") and router.quick("kakvo e cherna dupka") and router.quick("кажи ми виц")
    assert not router.quick("какви задачи имам днес")  # needs the agent's tools
    assert not router.quick("изпрати SMS на мама")
    assert router.think_harder("помисли добре как да подредя седмицата")


# --- through the brain -----------------------------------------------------------------------------------

@pytest.fixture
def routed(settings):
    settings.router = True
    settings.model = "claude-opus-5-5"
    return settings


def levels(ctx):
    return usage.levels_today(ctx.store)


def test_a_command_costs_no_ai_request(routed, ctx, registry, monkeypatch):
    from jarvis.plugins import daily

    opened = []
    monkeypatch.setattr(daily, "find_video", lambda q: ("abcdefghijk", "AC/DC - Back In Black"))
    monkeypatch.setattr(daily.webbrowser, "open", opened.append)
    client = FakeClient([])
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("пусни Back in Black") == "Пускам „AC/DC - Back In Black“."
    assert opened == ["https://www.youtube.com/watch?v=abcdefghijk"] and client.requests == []
    assert levels(ctx) == {"1": 1, "2": 0, "3": 0}
    assert [m["content"] for m in ctx.store.history("main")] == ["пусни Back in Black", "Пускам „AC/DC - Back In Black“."]


def test_a_command_still_asks_and_respects_permissions(routed, ctx, registry, monkeypatch):
    from jarvis.plugins import system

    ran = []
    monkeypatch.setattr(system, "run_capture", lambda cmd, **kw: ran.append(cmd))
    approver = Approver(answer=False)
    jarvis = Jarvis(routed, ctx.store, registry, approver, client=FakeClient([]))
    assert jarvis.ask("изключи компютъра") == "Добре, няма да го правя."
    assert len(approver.asked) == 1 and ran == []

    # an account without the computer group: the agent answers instead of the rule
    guest = SimpleNamespace(id=7, name="Иван", is_owner=False, allowed_groups=lambda: {"core", "web"}, ask_groups=set)
    client = FakeClient([response(text_block("Нямаш достъп до компютъра."))])
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("заключи компютъра", user=guest, conversation="g") == "Нямаш достъп до компютъра."
    assert ran == [] and len(client.requests) == 1


def test_a_reminder_by_voice_is_set_without_ai(routed, ctx, registry):
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=FakeClient([]))
    answer = jarvis.ask("напомни ми след 10 минути да изключа фурната")
    rem = ctx.store.query("SELECT text, at FROM reminders")[0]
    assert answer.startswith("Добре, ще ти напомня в ") and rem["text"] == "да изключа фурната"
    assert abs(datetime.fromisoformat(rem["at"]) - (datetime.now() + timedelta(minutes=10))) < timedelta(minutes=2)


def test_a_short_question_goes_to_the_cheapest_model_without_tools(routed, ctx, registry, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = FakeClient([response(text_block("Област, от която дори светлината не излиза."))])
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("какво е черна дупка") == "Област, от която дори светлината не излиза."
    request = client.requests[0]
    assert request["model"] == "claude-haiku-5-5" and "tools" not in request and request["output_config"] == {"effort": "low"}
    assert levels(ctx)["2"] == 1


def test_the_quick_model_hands_over_to_the_agent_when_it_needs_tools(routed, ctx, registry, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = FakeClient([response(text_block("ESCALATE")), response(text_block("Курсът е 1,95583 лева за евро."))])
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=client)
    assert jarvis.ask("колко струва еврото в левове") == "Курсът е 1,95583 лева за евро."
    assert client.requests[1]["model"] == "claude-opus-5-5" and client.requests[1]["tools"]
    assert levels(ctx) == {"1": 0, "2": 0, "3": 1}


def test_free_gemini_answers_the_quick_lane_even_when_claude_is_the_brain(routed, ctx, registry, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=FakeClient([]))
    assert jarvis.fast_model() == gemini.DEFAULT_MODEL
    routed.fast_model = "claude-haiku-5-5"
    assert jarvis.fast_model() == "claude-haiku-5-5"


def test_think_harder_raises_the_effort_for_one_request(routed, ctx, registry):
    client = FakeClient([response(text_block("План."))])
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=client)
    jarvis.ask("помисли добре как да подредя седмицата")
    assert client.requests[0]["output_config"] == {"effort": "max"}


def test_spending_is_told_without_ai(routed, ctx, registry):
    ctx.store.insert("usage", model="claude-opus-5-5", input=100_000, output=10_000)
    jarvis = Jarvis(routed, ctx.store, registry, Approver(), client=FakeClient([]))
    assert jarvis.ask("колко похарчих днес") == ("Днес похарчих 0,60 долара. Отговорих 0 пъти без AI, 0 пъти с бързия "
                                                 "модел и 0 пъти с пълния агент.")


# --- the daily cap and the brain on this computer ---------------------------------------------------------

def test_budget_setting():
    from jarvis.config import Settings

    s = Settings()
    for value, cap in [("", 1.0), ("2,5", 2.5), ("0", 0.0), ("без", None), ("abc", 1.0)]:
        s.daily_budget = value
        assert s.budget() == cap


class FakeOllama:
    def __init__(self, replies):
        self.replies = list(replies)
        self.bodies = []

    def __call__(self, path, body):
        self.bodies.append(json.loads(json.dumps(body)))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ollama_installed(monkeypatch, *names):
    monkeypatch.setattr(local, "_seen", {"at": float("inf"), "models": list(names)})


def test_after_the_daily_cap_claude_is_not_called(settings, ctx, registry, monkeypatch):
    from jarvis.hub import friendly_error

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings.model, settings.daily_budget = "claude-opus-5-5", "0.5"
    ctx.store.insert("usage", model="claude-opus-5-5", input=100_000, output=10_000)  # $0.60 today
    client = FakeClient([])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client)
    with pytest.raises(BudgetReached) as caught:
        jarvis.ask("здравей")
    assert client.requests == [] and "таван" in friendly_error(caught.value)

    ollama_installed(monkeypatch, "llama3.2:3b", "qwen3:4b")
    fake = FakeOllama([{"message": {"content": "<think>хм</think>Тук съм, от лаптопа."}, "prompt_eval_count": 900, "eval_count": 9}])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=client, local_brain=local.LocalBrain(fake))
    assert jarvis.ask("здравей") == "Тук съм, от лаптопа."
    assert fake.bodies[0]["model"] == "qwen3:4b" and client.requests == []
    last = ctx.store.query("SELECT model FROM usage ORDER BY id DESC LIMIT 1")[0]["model"]
    assert last == "local:qwen3:4b" and usage.price(last) == (0, 0, 0, 0)


def test_when_gemini_is_used_up_the_laptop_brain_answers_with_its_tools(settings, ctx, registry, monkeypatch):
    settings.model = gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    ollama_installed(monkeypatch, "qwen3:4b")
    daily = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                               "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
    google = SimpleNamespace(requests=[])

    def generate_content(model, contents, config):
        google.requests.append(model)
        raise daily

    google.models = SimpleNamespace(generate_content=generate_content)
    fake = FakeOllama([
        {"message": {"content": "", "tool_calls": [{"function": {"name": "remember", "arguments": {"topic": "кафе", "fact": "без захар"}}}]}},
        {"message": {"content": "Запомних."}, "done_reason": "stop"},
    ])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]),
                    gemini_brain=gemini.GeminiBrain(google, wait=lambda s: None), local_brain=local.LocalBrain(fake))
    assert jarvis.ask("запомни, че пия кафе без захар") == "Запомних."
    assert google.requests == gemini.FREE_MODELS  # every free model tried once, then the laptop
    first, second = fake.bodies
    assert {t["function"]["name"] for t in first["tools"]} <= local.LOCAL_TOOLS and first["options"]["num_ctx"] == 8192
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "remember"
    assert second["messages"][-1] == {"role": "tool", "tool_name": "remember", "content": "Remembered (id 1)."}
    assert ctx.store.query("SELECT fact FROM facts")[0]["fact"] == "без захар"


def test_a_laptop_model_that_cannot_think_or_use_tools_is_asked_again_without():
    def refused(text):
        return urllib.error.HTTPError("http://x", 400, "Bad Request", {}, io.BytesIO(text.encode()))

    fake = FakeOllama([refused('{"error":"model does not support thinking"}'), refused('{"error":"model does not support tools"}'),
                       {"message": {"content": "Добре."}}])
    answer = local.LocalBrain(fake).create(model="gemma3:4b", system="s", messages=[{"role": "user", "content": "здрасти"}],
                                           tools=[{"name": "weather", "description": "d", "input_schema": {"type": "object"}}],
                                           max_tokens=100, effort="max")
    assert answer.content[0].text == "Добре."
    assert "think" in fake.bodies[0] and "think" not in fake.bodies[1] and "tools" in fake.bodies[1]
    assert "tools" not in fake.bodies[2]


def test_switching_to_the_laptop_brain_needs_ollama(settings, ctx, registry, monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL", settings.model)  # the switch saves it; put it back afterwards
    output, is_error = registry.run("switch_brain", {"brain": "local"}, lambda s: True)
    assert is_error and "winget install Ollama.Ollama" in output
    ollama_installed(monkeypatch, "qwen3:4b")
    output, is_error = registry.run("switch_brain", {"brain": "local"}, lambda s: True)
    assert not is_error and settings.model == "local"


# --- the notes vault -------------------------------------------------------------------------------------

def test_the_vault_keeps_notes_in_three_folders(settings, ctx, registry):
    run = lambda name, **args: registry.run(name, args, lambda s: True)  # noqa: E731
    out, err = run("vault_note", text="# Идея\nJarvis с три нива")
    assert not err and out.startswith("Saved raw/")
    assert (settings.vault / "wiki" / "_master-index.md").exists() and (settings.vault / "output").is_dir()
    run("vault_write", path="wiki/ai/jarvis.md", text="Три нива: команди, бърз модел, агент")
    found, _ = run("vault_search", query="нива")
    assert "wiki/ai/jarvis.md" in found and "raw/" in found
    out, err = run("vault_write", path="../извън.md", text="x")
    assert err and "outside" in out
    raw = next((settings.vault / "raw").glob("*.md")).relative_to(settings.vault).as_posix()
    run("vault_done", path=raw)
    assert list((settings.vault / "raw" / "_done").glob("*.md"))
    assert registry.tools["vault_search"].group == "memory"
    assert "vault" in ROUTINES
