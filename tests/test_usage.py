from __future__ import annotations

from conftest import FakeClient, response, text_block
from jarvis import usage
from jarvis.brain import Jarvis


def test_laptop_answers_are_counted_per_model(settings, ctx, registry):
    answer = response(text_block("Готово, сър."))
    answer.usage = {"input": 5000, "output": 100, "cache_read": 0, "cache_write": 0}
    answer.model = "local:qwen3.5:9b"
    Jarvis(settings, ctx.store, registry, lambda s: True, client=FakeClient([answer])).ask("здравей")
    today = usage.summary(ctx.store)
    assert today["today"] == {"requests": 1, "tokens": 5100} and today["week"]["requests"] == 1
    assert today["models"] == [{"model": "local:qwen3.5:9b", "requests": 1, "tokens": 5100}]


def test_counting_never_breaks_an_answer(ctx):
    usage.record(ctx.store, object(), "local")  # no usage on the response: counted as zero
    usage.record(None, object(), "x")  # no store at all: logged, not raised
    assert ctx.store.query("SELECT model, input FROM usage") == [{"model": "local", "input": 0}]
