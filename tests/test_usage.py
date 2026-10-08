from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from conftest import FakeClient, response, text_block
from jarvis import usage
from jarvis.brain import Jarvis


def test_claude_answers_are_counted_and_priced(settings, ctx, registry):
    answer = response(text_block("Готово, сър."))
    answer.usage = SimpleNamespace(input_tokens=10_000, output_tokens=2_000, cache_read_input_tokens=100_000,
                                   cache_creation_input_tokens=0)
    answer.model = "claude-opus-5-5"
    settings.model = "claude-opus-5-5"
    Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([answer])).ask("здравей")
    today = usage.summary(ctx.store)
    # 10k in at $4 + 2k out at $20 + 100k cached at $0.20 per million tokens
    assert today["today"]["cost"] == pytest.approx(0.04 + 0.04 + 0.02)
    assert today["models"][0]["model"] == "claude-opus-5-5" and today["models"][0]["requests"] == 1


def test_free_gemini_requests_are_counted_per_model(settings, ctx, registry):
    pytest.importorskip("google.genai")
    from google.genai import types

    from jarvis import gemini
    from test_gemini import make, reply

    settings.model = gemini.DEFAULT_MODEL
    first = reply(types.Part(text="Тук съм."))
    first.usage_metadata = types.GenerateContentResponseUsageMetadata(prompt_token_count=5000, candidates_token_count=40,
                                                                      thoughts_token_count=60)
    jarvis, _ = make(settings, ctx, registry, [first])
    jarvis.ask("здравей")  # real work: the strongest free model answers
    jarvis.gemini.spent[gemini.DEFAULT_MODEL] = time.time() + 3600
    summary = usage.summary(ctx.store, jarvis.gemini.spent)
    lite = next(g for g in summary["gemini"] if g["model"] == gemini.DEFAULT_MODEL)
    flash = next(g for g in summary["gemini"] if g["model"] == gemini.SMART_MODELS[0])
    assert flash == {"model": gemini.SMART_MODELS[0], "today": 1, "used_up": False, "back_at": None}
    assert lite["used_up"] and lite["back_at"]
    assert summary["today"]["cost"] == 0 and summary["models"][0] | {"tokens": 5100} == summary["models"][0]


def test_counting_never_breaks_an_answer(ctx):
    usage.record(ctx.store, object(), "claude-opus-5-5")  # no usage on the response: counted as zero
    usage.record(None, object(), "x")  # no store at all: logged, not raised
    assert ctx.store.query("SELECT model, input FROM usage") == [{"model": "claude-opus-5-5", "input": 0}]
