from __future__ import annotations

import pytest

pytest.importorskip("google.genai")

from google.genai import errors, types  # noqa: E402

from conftest import FakeClient  # noqa: E402
from jarvis import gemini  # noqa: E402
from jarvis.brain import Jarvis  # noqa: E402


def reply(*parts, finish="STOP"):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=list(parts)), finish_reason=finish)])


def call(name, args, id="c1"):
    return types.Part(function_call=types.FunctionCall(id=id, name=name, args=args), thought_signature=b"signed")


class FakeGemini:
    """Stands in for google.genai.Client: scripted replies, recorded requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(settings, ctx, registry, replies, claude=None):
    fake = FakeGemini(replies)
    brain = gemini.GeminiBrain(fake, wait=lambda s: None)
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=claude or FakeClient([]), gemini_brain=brain)
    return jarvis, fake


def test_gemini_runs_the_tool_loop_and_sends_its_own_reply_back(settings, ctx, registry):
    settings.model, settings.effort = "gemini-3.8-flash", "xhigh"
    jarvis, fake = make(settings, ctx, registry, [
        reply(call("remember", {"topic": "кафе", "fact": "Пие кафе без захар"})),
        reply(types.Part(text="Запомних, сър.")),
    ])
    assert jarvis.ask("запомни, че пия кафе без захар") == "Запомних, сър."
    assert ctx.store.query("SELECT fact FROM facts")[0]["fact"] == "Пие кафе без захар"

    first, second = fake.requests
    names = {d.name for d in first["config"].tools[0].function_declarations}
    assert "remember" in names and "google_search" in names and "web_search" not in names
    assert first["config"].thinking_config.thinking_level == types.ThinkingLevel.HIGH  # xhigh: Gemini's highest
    assert "J.A.R.V.I.S." in first["config"].system_instruction
    model_turn, result_turn = second["contents"][-2:]
    assert model_turn.parts[0].thought_signature == b"signed"  # Gemini's reply goes back unchanged
    response = result_turn.parts[0].function_response
    assert (response.id, response.name) == ("c1", "remember") and "result" in response.response


def test_out_of_credit_claude_hands_over_to_gemini(settings, ctx, registry, monkeypatch):
    import anthropic

    monkeypatch.setenv("GEMINI_API_KEY", "free-key")
    settings.model = "claude-opus-5-5"
    claude = FakeClient([])

    def broke(**kwargs):
        err = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
        Exception.__init__(err, "Your credit balance is too low to access the Anthropic API.")
        raise err

    claude.beta.messages.create = broke
    jarvis, fake = make(settings, ctx, registry, [reply(types.Part(text="На линия съм, сър."))], claude=claude)
    assert jarvis.ask("здравей") == "На линия съм, сър."
    assert settings.model == gemini.DEFAULT_MODEL and fake.requests[0]["model"] == gemini.DEFAULT_MODEL


def test_without_a_gemini_key_the_credit_error_is_explained(settings, ctx, registry, monkeypatch):
    import anthropic

    from jarvis.hub import friendly_error

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY")
    claude = FakeClient([])

    def broke(**kwargs):
        err = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
        Exception.__init__(err, "Your credit balance is too low to access the Anthropic API.")
        raise err

    claude.beta.messages.create = broke
    jarvis, _ = make(settings, ctx, registry, [], claude=claude)
    with pytest.raises(anthropic.BadRequestError) as caught:
        jarvis.ask("здравей")
    assert "aistudio.google.com" in friendly_error(caught.value)
    assert "минута" in friendly_error(errors.ClientError(429, {"error": {"message": "RESOURCE_EXHAUSTED"}}))


def test_free_tier_limit_is_waited_out(settings, ctx, registry):
    settings.model = "gemini-3.8-flash"
    busy = errors.ClientError(429, {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}})
    jarvis, fake = make(settings, ctx, registry, [busy, reply(types.Part(text="Готово."))])
    assert jarvis.ask("здравей") == "Готово." and len(fake.requests) == 2


def test_camera_frames_and_claude_history_reach_gemini():
    history = [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/4AAQ"}},
            {"type": "text", "text": "какво виждаш"},
        ]},
        # Claude's own tool call in the middle of a task, before the brain changed
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tu_1", "name": "recall", "input": {"query": "кафе"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "без захар", "is_error": False}]},
    ]
    contents = gemini.to_contents(history)
    assert contents[0].parts[0].inline_data.mime_type == "image/jpeg" and contents[0].parts[1].text == "какво виждаш"
    assert "recall" in contents[1].parts[0].text and contents[1].role == "model"
    assert "без захар" in contents[2].parts[0].text and contents[2].parts[0].function_response is None


def test_switch_to_gemini_needs_its_key(settings, ctx, registry, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY")
    output, is_error = registry.run("switch_brain", {"brain": "gemini"}, lambda s: True, True, None, None)
    assert is_error and "aistudio.google.com" in output


def daily(model):
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": f"Quota exceeded, model: {model}",
                                              "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})


def test_when_a_free_model_is_used_up_for_the_day_the_next_one_answers(settings, ctx, registry):
    settings.model = gemini.DEFAULT_MODEL
    jarvis, fake = make(settings, ctx, registry, [
        daily(gemini.FREE_MODELS[0]), reply(types.Part(text="Тук съм.")), reply(types.Part(text="Пак съм тук.")),
    ])
    assert jarvis.ask("здравей") == "Тук съм."
    assert [r["model"] for r in fake.requests] == gemini.FREE_MODELS[:2]  # no waiting on a daily limit
    assert jarvis.ask("още ли си там") == "Пак съм тук."
    assert fake.requests[-1]["model"] == gemini.FREE_MODELS[1]  # the used-up model is skipped until tomorrow


def test_minute_limit_waits_as_long_as_google_says(settings, ctx, registry):
    settings.model = gemini.DEFAULT_MODEL
    waits = []
    busy = errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "per minute",
                                              "details": [{"retryDelay": "12s"}]}})
    fake = FakeGemini([busy, reply(types.Part(text="Готово."))])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([]),
                    gemini_brain=gemini.GeminiBrain(fake, wait=waits.append))
    assert jarvis.ask("здравей") == "Готово." and waits == [12.0]


def test_all_free_models_used_up_says_when_they_come_back(settings, ctx, registry):
    from jarvis.hub import friendly_error

    settings.model = gemini.DEFAULT_MODEL
    jarvis, _ = make(settings, ctx, registry, [daily(m) for m in gemini.FREE_MODELS])
    with pytest.raises(errors.ClientError) as caught:
        jarvis.ask("здравей")
    assert "утре" in friendly_error(caught.value)
