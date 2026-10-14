from __future__ import annotations

import io
import json
import time
import urllib.error
from types import SimpleNamespace

import pytest
from google.genai import errors

from conftest import Approver, FakeClient
from jarvis import local, freeapi, gemini, groq, usage
from jarvis import brain as brain_module
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
    assert openrouter == ["deepseek/deepseek-chat-v3.1:free", "meta-llama/llama-3.3-70b-instruct:free"]
    # only free ones that can use tools; paid gpt-5, the tiny helper and OpenRouter's own "free" router are left out


def test_nvidia_is_the_brain_with_every_tool_and_costs_nothing(settings, ctx, registry, nvidia_key):
    settings.model = "nvidia"
    fake = FakeAPI([said("", [call("remember", {"topic": "кафе", "fact": "без захар"})]), said("Запомних, сър.")])
    brain = freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), free_brains={"nvidia": brain})
    assert jarvis.ask("запомни, че пия кафе без захар") == "Запомних, сър."
    first, second = fake.bodies
    assert first["model"] == "moonshotai/kimi-k2-instruct-0905" and first["tool_choice"] == "auto"
    names = {t["function"]["name"] for t in first["tools"]}
    everything = {t["name"] for t in registry.definitions(None)}
    assert "remember" in names and len(names) > len(local.LOCAL_TOOLS)  # more than Groq's short list ...
    assert names < everything and not names & {"call", "send_sms", "pay"}  # ... but not the phone and money tools
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


def test_a_provider_that_runs_out_of_time_twice_rests_as_a_whole(nvidia_key, monkeypatch):
    """Yesterday's log: 105 read timeouts in a day, each one waited 45 seconds and then asked the next model of
    the same slow queue. After two timeouts the whole provider rests and the next free brain answers at once."""
    rests = []
    monkeypatch.setattr(freeapi, "SLOW_WINDOW", 900)

    def slow(body):
        raise urllib.error.URLError("timed out")

    brain = freeapi.FreeBrain("nvidia", post=slow, get=lambda: NVIDIA_LIST, wait=rests.append)
    for _ in range(2):
        with pytest.raises(freeapi.UsedUp):
            brain.create(model="nvidia", system="", messages=[{"role": "user", "content": "ехо"}], tools=[],
                         max_tokens=100, effort="low")
    assert brain.models("nvidia") == [] and brain.resting["*"] > time.time() + freeapi.SLOW_REST - 5
    assert brain.timeouts == [] and rests == []  # no waiting on top of the timeouts themselves


def test_free_brains_get_the_tools_the_request_is_about(registry):
    """A hundred tool schemas drown a small free model: it talks about the tools instead of calling them. A
    plain request gets the always-on groups; the phone, mail and money tools join when the words call for them;
    a mission keeps everything."""
    tools = registry.definitions(None)
    groups = {name: (tool.group or "agent") for name, tool in registry.tools.items()}

    def picked(text):
        return {groups[t["name"]] for t in brain_module.select_tools(tools, groups, text)}

    assert picked("какво е времето") == brain_module.ALWAYS_GROUPS
    assert {"web_browser", "click_text", "remember", "connections"} <= {t["name"] for t in brain_module.select_tools(
        tools, groups, "какво е времето")}
    assert picked("svurji telefona mi") >= {"android", "agent"} and "comms" not in picked("svurji telefona mi")
    assert picked("обади се на мама") >= {"android", "comms"}
    assert picked("плати фактурата по paypal") >= {"payments", "sendmoney"}
    assert picked("работи докато не стане: намери клиенти") == set(groups.values())
    assert brain_module.select_tools(tools, {}, "ехо") == tools  # unknown groups: send everything rather than nothing


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


def test_a_gemini_call_google_cancels_mid_task_goes_to_the_next_brain(settings, ctx, registry, nvidia_key, monkeypatch):
    """The owner's screen: six minutes into a mission Gemini answered '499 CANCELLED' and the whole task ended
    with that error. A dropped call is a busy brain: the next model, then the next brain, carries on."""
    settings.model = gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    cancelled = errors.APIError(499, {"error": {"code": 499, "message": "The operation was cancelled.", "status": "CANCELLED"}})
    asked = []

    def generate_content(model, contents, config):
        asked.append(model)
        raise cancelled

    brain = gemini.GeminiBrain(SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), wait=lambda s: None)
    fake = FakeAPI([said("Продължавам през NVIDIA.")] * 3)
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), gemini_brain=brain,
                    free_brains={"nvidia": freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)})
    assert jarvis.ask("направи го") == "Продължавам през NVIDIA."
    assert asked and set(asked) <= set([gemini.AGENT_MODEL, *gemini.FREE_MODELS])  # every Gemini model tried, each resting
    # an error no brain recognises is still not the end while another brain answers
    def broken(model, contents, config):
        raise RuntimeError("bad reply from Google")
    brain = gemini.GeminiBrain(SimpleNamespace(models=SimpleNamespace(generate_content=broken)), wait=lambda s: None)
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), gemini_brain=brain,
                    free_brains={"nvidia": freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)})
    assert jarvis.ask("направи го") == "Продължавам през NVIDIA."


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


def test_gemini_flash_does_the_real_work_while_its_day_lasts_then_the_strong_free_models(settings, ctx, registry,
                                                                                         nvidia_key, monkeypatch):
    """The owner's laptop: Flash (which sees the screen and carries a task) sat unused while NVIDIA's and
    OpenRouter's small models answered. Flash goes first while it has free requests; once its day is spent
    the strong free models go before Flash-Lite; a scheduled job nobody watches never spends Flash."""
    from google.genai import types
    from jarvis.brain import free_brain

    settings.model = gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert free_brain() == "nvidia"
    asked = []

    def generate_content(model, contents, config):
        asked.append(model)
        return types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text="Свърших го през Gemini.")]), finish_reason="STOP")])

    brain = gemini.GeminiBrain(SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), wait=lambda s: None)
    fake = FakeAPI([said("Свърших го през NVIDIA.")] * 4)
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]), gemini_brain=brain,
                    free_brains={"nvidia": freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)})
    assert jarvis.ask("направи го") == "Свърших го през Gemini."
    assert set(asked) == {gemini.AGENT_MODEL} and fake.bodies == []
    brain.spent[gemini.AGENT_MODEL] = time.time() + 3600  # Flash's free day is over: NVIDIA before Flash-Lite
    assert jarvis.ask("направи го пак") == "Свърших го през NVIDIA." and set(asked) == {gemini.AGENT_MODEL}
    brain.spent.clear()
    job = "(Scheduled job, the user is not watching; report the outcome briefly.) провери пощата"
    assert jarvis.ask(job, route=False) == "Свърших го през NVIDIA." and set(asked) == {gemini.AGENT_MODEL}
    # A job on web pages (the fast lane, a dozen tools) stays on quick Gemini: NVIDIA took ~20 s a step there.
    before = len(fake.bodies)
    assert jarvis.ask("виж в браузъра кога играе Левски и срещу кого") == "Свърших го през Gemini."
    assert len(fake.bodies) == before  # NVIDIA was not asked


def test_a_slow_queue_hands_over_at_once_and_cheap_refusals_cost_no_tries(nvidia_key, tmp_path):
    def timing_out(body):
        timing_out.bodies.append(body["model"])
        raise urllib.error.URLError("The read operation timed out")
    timing_out.bodies = []
    state = tmp_path / "resting-nvidia.json"
    brain = freeapi.FreeBrain("nvidia", post=timing_out, get=lambda: NVIDIA_LIST, state=state)
    with pytest.raises(freeapi.UsedUp, match="timed out"):
        brain.create(model="nvidia", system="", messages=[], tools=[], max_tokens=100, effort="low")
    assert timing_out.bodies == ["moonshotai/kimi-k2-instruct-0905"]  # one timeout, not three in a row
    assert brain.timeout == freeapi.QUICK_TIMEOUT  # no tools: a short answer is waited for briefly
    # Models the account cannot use (404) are skipped for free: they do not use up the tries.
    fake = FakeAPI([(404, "not for this account")] * 5 + [(503, "down")] * 3)
    brain = freeapi.FreeBrain("nvidia", post=fake, get=lambda: NVIDIA_LIST)
    with pytest.raises(freeapi.UsedUp, match="503"):
        brain.create(model="nvidia", system="", messages=[], tools=[{"name": "x", "input_schema": {}}],
                     max_tokens=100, effort="low")
    assert len(fake.bodies) == 7 and brain.timeout == freeapi.TIMEOUT  # all 7 listed models asked, 5 for free
    # Resting is remembered across restarts, so a gone or slow model is not asked again on every start.
    again = freeapi.FreeBrain("nvidia", post=FakeAPI([said("Тук.")]), get=lambda: NVIDIA_LIST, state=state)
    assert "moonshotai/kimi-k2-instruct-0905" in again.resting
    assert again.models("nvidia")[0] == "moonshotai/kimi-k2-instruct"


def test_a_model_that_keeps_running_out_of_time_rests_a_day_until_it_answers_again(nvidia_key, tmp_path):
    """The owner's NVIDIA account has only slow thinkers (deepseek-v4.1-flash, glm-5.3): 45 s waited for each on
    every request made every answer a minute late. Ten minutes' rest the first time, six hours the second, a
    day from the third; remembered across restarts; forgiven once the model answers in time."""
    def timing_out(body):
        raise urllib.error.URLError("The read operation timed out")
    listing = [{"id": "deepseek-ai/deepseek-v4.1-flash"}]
    state = tmp_path / "resting-nvidia.json"
    brain = freeapi.FreeBrain("nvidia", post=timing_out, get=lambda: listing, state=state)
    rests = []
    for _ in range(3):
        with pytest.raises(freeapi.UsedUp):
            brain.create(model="nvidia", system="", messages=[], tools=[], max_tokens=100, effort="low")
        rests.append(brain.resting["deepseek-ai/deepseek-v4.1-flash"] - time.time())
        brain.resting.clear()
    assert 500 < rests[0] <= 600 and 6 * 3600 - 100 < rests[1] <= 6 * 3600 and 86400 - 100 < rests[2] <= 86400
    again = freeapi.FreeBrain("nvidia", post=FakeAPI([said("Най-после.")]), get=lambda: listing, state=state)
    assert again.slow == {"deepseek-ai/deepseek-v4.1-flash": 3}
    assert again.models("nvidia") == []  # it rests: the provider is skipped at once, the next brain answers now
    again.resting.clear()
    assert again.create(model="nvidia", system="", messages=[], tools=[], max_tokens=100, effort="low").content[0].text == "Най-после."
    assert again.slow == {} and json.loads(state.read_text())["slow"] == {}
    # The old state file (models only) still reads.
    state.write_text(json.dumps({"z-ai/glm-5.3": time.time() + 999}))
    assert "z-ai/glm-5.3" in freeapi.FreeBrain("nvidia", post=timing_out, get=lambda: listing, state=state).resting


def test_long_thinkers_go_last():
    listing = [{"id": i} for i in ["deepseek-ai/deepseek-v4.1-flash", "z-ai/glm-5.3-flash", "moonshotai/kimi-k2-instruct",
                                   "deepseek-ai/deepseek-v3.2"]]
    assert freeapi.choose(freeapi.PROVIDERS["nvidia"], listing) == [
        "moonshotai/kimi-k2-instruct", "deepseek-ai/deepseek-v3.2", "deepseek-ai/deepseek-v4.1-flash", "z-ai/glm-5.3-flash"]


def test_auto_picks_the_brain_for_every_step_and_switches_in_the_middle_of_a_task(settings, ctx, registry, monkeypatch):
    """Settings > Мозък > Авто: a quick brain for easy steps, the smartest after a failed step or for a mission,
    Gemini for a picture, Groq for a short answer; chosen again at every step."""
    from jarvis.hub import brain_scale

    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setenv(key, "x")
    settings.model = "auto"
    jarvis = Jarvis(settings, ctx.store, registry, Approver(), client=FakeClient([]))
    monkeypatch.setattr(jarvis, "flash_ready", lambda: True)
    asked = []
    monkeypatch.setattr(jarvis, "_ask_free", lambda order, *a, **kw: asked.append(order) or SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Готово.")], stop_reason="end_turn"))
    tools = [{"name": "open_page"}]
    easy = [{"role": "user", "content": "виж в браузъра курса на долара"}]
    failed = easy + [{"role": "assistant", "content": "…"},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "Timeout", "is_error": True}]}]
    picture = [{"role": "user", "content": [{"type": "image", "source": {}}, {"type": "text", "text": "какво е това"}]}]
    jarvis._request(easy, tools=tools)
    jarvis._request(failed, tools=tools)  # the same task, next step: the smartest takes over
    jarvis._request(picture, tools=tools)
    jarvis._request(easy, tools=[])
    jarvis._request([{"role": "user", "content": "работи докато не стане: намери клиенти"}], tools=tools)
    step = [{"role": "assistant", "content": "…"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}]
    jarvis._request(easy + step * 3, tools=tools)  # still quick
    jarvis._request(easy + step * 4, tools=tools)  # a long task: the smartest takes over
    assert [order[0] for order in asked] == [gemini.DEFAULT_MODEL, gemini.AGENT_MODEL, gemini.DEFAULT_MODEL,
                                             groq.DEFAULT_MODEL, gemini.AGENT_MODEL, gemini.DEFAULT_MODEL,
                                             gemini.AGENT_MODEL]
    assert "nvidia" in asked[1] and asked[1].index("nvidia") < asked[1].index(gemini.DEFAULT_MODEL)
    monkeypatch.delenv("NVIDIA_API_KEY")
    jarvis._request(failed, tools=tools)
    assert "nvidia" not in asked[-1]  # only the brains with a key
    assert brain_scale({"JARVIS_MODEL": "auto"})["auto"] and not brain_scale({"JARVIS_MODEL": "groq"})["auto"]
