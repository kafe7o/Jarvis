from types import SimpleNamespace

from jarvis.assistant import Jarvis
from jarvis.tools import run_tool


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _tool(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append([dict(m) for m in kwargs["messages"]])
        return self.responses.pop(0)


def test_keeps_history_and_runs_tools():
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[_tool("get_current_time", {})]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Часът е 12:00.")]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Пак заповядайте.")]),
    ])
    j = Jarvis(client=client, model="test")
    assert j.ask("Колко е часът?") == "Часът е 12:00."
    tool_result = client.calls[1][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and not tool_result["is_error"]
    assert j.ask("Благодаря") == "Пак заповядайте."
    assert len(client.calls[2]) == 5  # въпрос, tool_use, резултат, отговор, нов въпрос


def test_history_rolled_back_on_error():
    class Boom(FakeClient):
        def create(self, **kwargs):
            raise RuntimeError("API down")

    j = Jarvis(client=Boom([]), model="test")
    try:
        j.ask("здравей")
    except RuntimeError:
        pass
    assert j.history == []


def test_unknown_tool_is_error():
    out, is_error = run_tool("nope", {})
    assert is_error


def test_bad_timezone():
    out, is_error = run_tool("get_current_time", {"timezone": "Mars/Base"})
    assert "Непозната" in out


def test_memory_is_saved_and_fed_into_prompt():
    from jarvis.memory import MemoryStore

    memory = MemoryStore(":memory:")
    calls = []

    class Client(FakeClient):
        def create(self, **kwargs):
            calls.append(kwargs)
            return super().create(**kwargs)

    client = Client([
        SimpleNamespace(stop_reason="tool_use", content=[
            _tool("remember_fact", {"text": "Кучето на Анастас се казва Рекс", "category": "person"})
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Запомних.")]),
    ])
    j = Jarvis(client=client, model="test", memory=memory)
    assert j.ask("Кучето ми се казва Рекс") == "Запомних."
    assert "remember_fact" in [t["name"] for t in calls[0]["tools"]]
    assert not client.calls[1][-1]["content"][0]["is_error"]

    # Нова сесия със същата памет: фактът е в system prompt-а.
    client2 = Client([SimpleNamespace(stop_reason="end_turn", content=[_text("Рекс.")])])
    Jarvis(client=client2, model="test", memory=memory).ask("Как се казва кучето?")
    assert "Рекс" in calls[-1]["system"]
