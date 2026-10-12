"""Taking the screen over move by move (the Clash Royale video): look, one move, look again."""

from __future__ import annotations

from conftest import Approver

from jarvis import screenwork
from jarvis.screenwork import parse_move, run

PNG = b"\x89PNG fake"


def test_the_move_is_read_from_the_brain_text_fences_and_chatter_aside():
    move = parse_move('Sure.\n```json\n{"see": "a card", "action": "Click", "x": 120.4, "y": 300, "say": "Играя картата"}\n```')
    assert move["action"] == "click" and move["x"] == 120 and move["y"] == 300 and move["say"] == "Играя картата"
    drag = parse_move('{"action": "drag", "x": 1, "y": 2, "x2": 30, "y2": 40, "say": "влача"}')
    assert (drag["x2"], drag["y2"]) == (30, 40)
    keys = parse_move('{"action": "hotkey", "keys": "ctrl+s", "say": "запис"}')
    assert keys["keys"] == ["ctrl", "s"]
    scroll = parse_move('{"action": "scroll", "say": "надолу",}')  # a trailing comma is forgiven
    assert scroll["action"] == "scroll" and scroll["amount"] == -5


def test_an_unusable_answer_counts_as_stuck_not_as_a_guessed_click():
    assert parse_move("I cannot see the screen.")["action"] == "stuck"
    assert parse_move('{"action": "click", "say": "no coordinates"}')["action"] == "stuck"
    assert parse_move('{"action": "fly", "x": 1, "y": 1}')["action"] == "stuck"
    assert parse_move('{"action": "click", "x": "left", "y": 1}')["action"] == "stuck"


def make(answers):
    answers = list(answers)
    made = []

    def decide(prompt, image):
        assert image == PNG and "Goal: " in prompt
        return answers.pop(0)

    return decide, made, lambda move: made.append(move)


def test_the_loop_plays_until_the_brain_sees_the_goal_reached():
    decide, made, act = make([
        '{"action": "click", "x": 10, "y": 20, "say": "натискам Battle"}',
        '{"action": "wait", "say": "зарежда"}',
        '{"action": "drag", "x": 5, "y": 5, "x2": 50, "y2": 60, "say": "пускам рицаря"}',
        '{"action": "done", "say": "Спечелих рунда"}',
    ])
    events = []
    out = run("win the round", lambda: (PNG, (640, 360)), decide, act, pause=0, report=events.append)
    assert out.startswith("Готово след 3 хода: Спечелих рунда")
    assert [m["action"] for m in made] == ["click", "wait", "drag"]
    assert "натискам Battle" in out and "пускам рицаря" in out
    assert [e["state"] for e in events if e.get("tool")] == ["running"] * 4  # every move shows on the live screen
    assert events[-1]["state"] == "ok"


def test_the_loop_stops_when_stuck_on_a_repeated_move_or_when_the_moves_run_out():
    decide, made, act = make(['{"action": "stuck", "say": "Иска парола"}'])
    out = run("log in", lambda: (PNG, (640, 360)), decide, act, pause=0)
    assert out.startswith("Спрях на ход 1: Иска парола") and not made

    same = '{"action": "click", "x": 10, "y": 20, "say": "пак"}'
    decide, made, act = make([same, same, same, same])
    out = run("x", lambda: (PNG, (640, 360)), decide, act, pause=0)
    assert "същият ход трети път" in out and len(made) == 2

    decide, made, act = make(['{"action": "scroll", "amount": -3, "say": "надолу"}'] * 5)
    out = run("x", lambda: (PNG, (640, 360)), decide, act, steps=2, pause=0)
    assert out.startswith("Направих 2 хода и спрях") and len(made) == 2


def test_stop_and_a_failed_move_do_not_end_in_a_traceback():
    decide, made, act = make(['{"action": "click", "x": 1, "y": 1, "say": "а"}', '{"action": "done", "say": "б"}'])
    halts = iter([False, True])
    out = run("x", lambda: (PNG, (640, 360)), decide, act, pause=0, stopped=lambda: next(halts))
    assert out.startswith("Спрях, както каза") and len(made) == 1

    def bad_act(move):
        raise ValueError("(9999, 1) is outside the screenshot")

    decide, _made, _act = make(['{"action": "click", "x": 9999, "y": 1, "say": "а"}', '{"action": "done", "say": "б"}'])
    out = run("x", lambda: (PNG, (640, 360)), decide, bad_act, pause=0)
    assert out.startswith("Готово след 1 хода") and "failed: (9999, 1) is outside" in out

    def broken_brain(prompt, image):
        raise RuntimeError("no brain")

    out = run("x", lambda: (PNG, (640, 360)), broken_brain, act, pause=0)
    assert "мозъкът не отговори (RuntimeError)" in out


def test_the_tool_asks_once_then_plays_on_its_own(settings, ctx, registry, monkeypatch):
    from types import SimpleNamespace

    from jarvis.brain import Jarvis

    shots, asked, moves = [], [], []
    answers = iter(['{"action": "click", "x": 3, "y": 4, "say": "едно"}', '{"action": "done", "say": "готово"}'])
    jarvis = Jarvis(settings, ctx.store, registry, Approver(True))
    ctx.jarvis = jarvis

    def request(messages, user, system, allowed, **kw):  # the seeing brain gets only the picture and the goal
        asked.append((messages, system, kw))
        assert messages[0]["content"][0]["type"] == "image" and kw["tools"] == []
        assert system == screenwork.SYSTEM
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=next(answers))], stop_reason="end_turn")

    monkeypatch.setattr(jarvis, "_request", request)
    fake = SimpleNamespace(screenshot=lambda: SimpleNamespace(size=(640, 360), resize=lambda s: None,
                                                              convert=lambda m: SimpleNamespace(save=lambda buf, **k: buf.write(PNG))),
                           click=lambda x, y: moves.append(("click", x, y)))
    monkeypatch.setitem(__import__("sys").modules, "pyautogui", fake)
    monkeypatch.setattr(screenwork.time, "sleep", lambda s: shots.append(s))
    approver = Approver(True)
    out, err = registry.run("work_on_screen", {"goal": "натисни бутона", "steps": 5}, approver)
    assert not err and out.startswith("Готово след 1 хода: готово"), out
    assert approver.asked == ["Поема екрана: натисни бутона"]  # one yes for the whole game, not one per move
    assert moves == [("click", 3, 4)] and len(asked) == 2
