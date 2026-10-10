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
    assert settings.model == gemini.DEFAULT_MODEL and fake.requests[0]["model"] == gemini.AGENT_MODEL  # real work: Flash
    from jarvis.setup_wizard import env_path, read_env

    assert read_env(env_path())["JARVIS_MODEL"] == gemini.DEFAULT_MODEL  # the next start does not try Claude again


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
    # another model's call is a note on the user's side, never words of its own it could copy
    assert len(contents) == 1 and contents[0].parts[2].function_response is None
    note = contents[0].parts[2].text
    assert "recall" in note and "без захар" in note and "I used the tool" not in note


def test_a_call_written_as_text_is_never_the_answer(settings, ctx, registry):
    settings.model = "gemini-3.8-flash"
    old = [{"role": "user", "content": "пусни музика"}, {"role": "assistant", "content": "[I used the tool look_at_screen with {}]"}]
    assert len(gemini.to_contents(old)) == 1  # an old answer that was only a call written as text is left out
    jarvis, fake = make(settings, ctx, registry, [
        reply(types.Part(text="[I used the tool look_at_screen with {}]")),  # written, not made: asked again
        reply(types.Part(text="Тук съм.")),
    ])
    assert jarvis.ask("здравей") == "Тук съм." and len(fake.requests) == 2


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
        daily(gemini.AGENT_MODEL), reply(types.Part(text="Тук съм.")), reply(types.Part(text="Пак съм тук.")),
    ])
    assert jarvis.ask("здравей") == "Тук съм."
    # real work starts on the stronger Flash; its daily limit hands over at once to Flash-Lite
    assert [r["model"] for r in fake.requests] == [gemini.AGENT_MODEL, gemini.DEFAULT_MODEL]
    assert jarvis.ask("още ли си там") == "Пак съм тук."
    assert fake.requests[-1]["model"] == gemini.DEFAULT_MODEL  # the used-up model is skipped until tomorrow


def minute(delay):
    """This minute's share is used up, e.g. 250,000 tokens of input (each step sends the whole task again)."""
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "per minute", "details": [
        {"quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier"}, {"retryDelay": delay}]}})


def test_a_minute_limit_hands_over_at_once_and_only_a_short_one_is_waited(settings, ctx, registry):
    import time

    settings.model = gemini.DEFAULT_MODEL
    waits = []
    fake = FakeGemini([minute("48s"), minute("3s"), reply(types.Part(text="Готово."))])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([]),
                    gemini_brain=gemini.GeminiBrain(fake, wait=waits.append))
    assert jarvis.ask("здравей") == "Готово."
    assert [r["model"] for r in fake.requests] == [gemini.AGENT_MODEL] + [gemini.DEFAULT_MODEL] * 2
    assert waits == [3.0]  # not 48 seconds twice: Flash-Lite answered meanwhile
    assert 40 < jarvis.gemini.resting[gemini.AGENT_MODEL] - time.time() <= 48 and not jarvis.gemini.spent


def test_when_every_model_is_at_its_minute_limit_jarvis_waits_for_the_first_one(settings, ctx, registry):
    settings.model = gemini.DEFAULT_MODEL
    models = gemini.GeminiBrain().candidates(gemini.DEFAULT_MODEL, agent=True)
    fake = FakeGemini([minute("30s")] * len(models) + [reply(types.Part(text="Тук съм."))])
    brain = gemini.GeminiBrain(fake, wait=lambda s: None)
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([]), gemini_brain=brain)
    waited = []
    jarvis.wait = lambda s: (waited.append(s), brain.resting.clear())  # the minute passes
    assert jarvis.ask("здравей") == "Тук съм."  # no other free brain is set up: no error, a short wait
    assert len(waited) == 1 and 25 < waited[0] <= 31
    assert [r["model"] for r in fake.requests] == [*models, gemini.AGENT_MODEL]


def test_older_tool_results_and_screenshots_are_cut_to_save_room():
    import base64

    picture = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                           "data": base64.b64encode(b"png").decode()}}
    messages = [{"role": "user", "content": "направи го"}]
    for n in range(6):
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{n}", "name": "look_at_screen", "input": {}}]})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{n}",
                                                      "content": [{"type": "text", "text": "x" * 5000}, picture]}]})
    contents = gemini.to_contents(messages, gemini.DEFAULT_MODEL)
    notes = [p.text for c in contents if c.role == "user" for p in c.parts if p.text and "look_at_screen" in p.text]
    assert len(notes) == 6
    assert all("left out to save room" in n and len(n) < 2000 for n in notes[:2])  # the two oldest steps
    assert all("x" * 4000 in n and "left out" not in n for n in notes[2:])  # the last four in full
    pictures = [p for c in contents for p in c.parts if p.inline_data]
    assert len(pictures) == 4  # only the latest screenshots go along


def test_all_free_models_used_up_says_when_they_come_back(settings, ctx, registry):
    from jarvis.hub import friendly_error

    settings.model = gemini.DEFAULT_MODEL
    jarvis, _ = make(settings, ctx, registry, [daily(m) for m in gemini.FREE_MODELS])
    with pytest.raises(gemini.UsedUp) as caught:
        jarvis.ask("здравей")
    assert "Връщат се в" in friendly_error(caught.value) and "Ollama" in friendly_error(caught.value)


def test_claude_can_take_over_in_the_middle_of_a_gemini_turn(settings, ctx, registry, monkeypatch):
    """Gemini switches the brain to Claude mid-turn; Claude gets Gemini's blocks as plain JSON."""
    import json

    from conftest import response, text_block

    monkeypatch.setenv("GEMINI_API_KEY", "free-key")
    monkeypatch.setattr(settings, "model", "gemini-3.8-flash")
    claude = FakeClient([response(text_block("Вече съм на Haiku, сър."))])
    jarvis, _ = make(settings, ctx, registry, [reply(call("switch_brain", {"brain": "haiku"}))], claude=claude)
    assert jarvis.ask("ползвай най-евтиния модел") == "Вече съм на Haiku, сър."
    sent = claude.requests[0]["messages"]
    json.dumps(sent)  # the real SDK serializes this; Block objects used to crash it
    assert sent[1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "switch_brain", "input": {"brain": "haiku"}}


def test_without_a_chosen_brain_jarvis_starts_on_a_free_one(monkeypatch):
    from jarvis.config import Settings

    for name in ("JARVIS_MODEL", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert Settings().model == "claude-opus-5-5"  # no free key: the paid brain is all there is
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert Settings().model == "groq"
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert Settings().model == gemini.DEFAULT_MODEL
    monkeypatch.setenv("JARVIS_MODEL", "claude-sonnet-5-5")
    assert Settings().model == "claude-sonnet-5-5"  # only when the owner chose Claude


def test_real_work_runs_on_flash_and_short_answers_on_flash_lite(settings, ctx, registry):
    settings.model = gemini.DEFAULT_MODEL
    fake = FakeGemini([reply(types.Part(text="Париж.")), reply(types.Part(text="Тук съм."))])
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([]),
                    gemini_brain=gemini.GeminiBrain(fake, wait=lambda s: None))
    assert jarvis.quick_answer("коя е столицата на Франция?", "main") == "Париж."
    assert jarvis.ask("здравей") == "Тук съм."
    assert [r["model"] for r in fake.requests] == [gemini.DEFAULT_MODEL, gemini.AGENT_MODEL]
    assert fake.requests[0]["config"].tools is None and fake.requests[1]["config"].tools


def test_a_slow_or_broken_answer_goes_to_the_next_model_instead_of_an_error(settings, ctx, registry):
    settings.model = gemini.DEFAULT_MODEL
    slow = errors.ServerError(504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED", "message": "Deadline expired"}})
    jarvis, fake = make(settings, ctx, registry, [
        slow,  # Flash too slow: Flash-Lite at once, no waiting
        reply(finish="MALFORMED_FUNCTION_CALL"),  # a broken tool call: asked once more
        reply(call("remember", {"topic": "кафе", "fact": "без захар"})),
        reply(types.Part(text="Запомних.")),
    ])
    assert jarvis.ask("запомни, че пия кафе без захар") == "Запомних."
    assert [r["model"] for r in fake.requests] == [gemini.AGENT_MODEL] + [gemini.DEFAULT_MODEL] * 3
    assert ctx.store.query("SELECT fact FROM facts")[0]["fact"] == "без захар"
    assert jarvis.gemini.models(gemini.DEFAULT_MODEL, agent=True)[0] == gemini.DEFAULT_MODEL  # Flash rests a moment
    assert not jarvis.gemini.spent  # a slow moment is not a used-up day


def test_a_model_this_key_cannot_use_is_skipped(settings, ctx, registry):
    settings.model = gemini.DEFAULT_MODEL
    missing = errors.ClientError(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "model not found"}})
    jarvis, fake = make(settings, ctx, registry, [missing, reply(types.Part(text="Тук съм."))])
    assert jarvis.ask("здравей") == "Тук съм."
    assert [r["model"] for r in fake.requests] == [gemini.AGENT_MODEL, gemini.DEFAULT_MODEL]


def test_when_gemini_is_overloaded_another_free_brain_answers(settings, ctx, registry, monkeypatch):
    from jarvis import groq
    from jarvis.hub import friendly_error

    settings.model = gemini.DEFAULT_MODEL
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    overloaded = errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}})
    google = FakeGemini([overloaded] * 12)
    answers = [{"choices": [{"message": {"content": "Тук съм, през Groq."}, "finish_reason": "stop"}], "usage": {}}]
    jarvis = Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([]),
                    gemini_brain=gemini.GeminiBrain(google, wait=lambda s: None),
                    groq_brain=groq.GroqBrain(lambda body: answers.pop(0)))
    with pytest.raises(gemini.Busy) as caught:  # no other free brain yet: says Gemini is overloaded, not used up
        jarvis.ask("здравей")
    assert "претоварен" in friendly_error(caught.value)
    jarvis.gemini.resting.clear()
    google.replies = [overloaded] * 12
    monkeypatch.setenv("GROQ_API_KEY", "free-key")
    assert jarvis.ask("здравей") == "Тук съм, през Groq."
