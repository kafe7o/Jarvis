"""Working the screen on a loop: look, decide one move, make it, look again.

This is how Jarvis "takes over" something with no words to click (a game, a map, a drawing, a video
player): a seeing brain gets only the newest screenshot and the goal, answers with one move as JSON,
the move is made, and the next screenshot shows what it did. Nothing of the conversation travels with
each step, so a move costs one small request and comes back in seconds. The loop ends when the brain
says the goal is reached or that it is stuck, when the owner says "спри", or after ``steps`` moves.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

log = logging.getLogger("jarvis.screen")

ACTIONS = {"click", "double_click", "right_click", "move", "drag", "scroll", "type", "hotkey", "press", "wait",
           "done", "stuck"}
NEEDS_POINT = {"click", "double_click", "right_click", "move", "drag"}

SYSTEM = """You control a computer's mouse and keyboard to reach a goal on its screen, one move at a time. \
You see the newest screenshot; coordinates are pixels of that image (x to the right, y down). \
Answer with ONE JSON object and nothing else:
{"see": "<one line: what on the screen matters for the goal>",
 "action": "click | double_click | right_click | move | drag | scroll | type | hotkey | press | wait | done | stuck",
 "x": 0, "y": 0, "x2": 0, "y2": 0, "text": "", "keys": [], "amount": -5,
 "say": "<one short line, in the owner's language, of what you do now>"}
Rules: click, double_click, right_click and move need x and y; drag needs x, y, x2, y2; scroll takes amount \
(negative scrolls down) and optional x, y; type takes text; hotkey takes keys pressed together (e.g. \
["ctrl","s"]); press takes keys one after another (e.g. ["enter"]); wait when something is loading or an \
animation plays. Say done when the screenshot shows the goal is reached, with "say" telling the result. \
Say stuck when you cannot see how to go on (a login, a dialog that needs the owner, a screen you do not \
understand), with "say" telling why. Make the one move that brings the goal nearest; after a move that \
changed nothing, do something different. Never type passwords or pay for anything."""

MOVE_PROMPT = ("Goal: {goal}\n{history}The screenshot is {width}x{height} pixels. Your one next move, as JSON.")


def parse_move(text: str) -> dict:
    """The move in the brain's answer: the first JSON object in it, fences and chatter aside. An answer with no
    usable move counts as stuck (the loop stops instead of guessing a click)."""
    text = (text or "").strip()
    found = re.search(r"\{.*\}", text, re.S)
    move: dict = {}
    if found:
        raw = found.group()
        for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
            try:
                loaded = json.loads(candidate)
                if isinstance(loaded, dict):
                    move = loaded
                    break
            except json.JSONDecodeError:
                continue
    action = str(move.get("action") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if action not in ACTIONS:
        return {"action": "stuck", "say": move.get("say") or text[:200] or "no move", "see": move.get("see", "")}
    if action in NEEDS_POINT and (move.get("x") is None or move.get("y") is None):
        return {"action": "stuck", "say": f"{action} without coordinates", "see": move.get("see", "")}
    if action == "drag" and (move.get("x2") is None or move.get("y2") is None):
        return {"action": "stuck", "say": "drag without a target", "see": move.get("see", "")}
    move["action"] = action
    for key in ("x", "y", "x2", "y2", "amount"):
        if move.get(key) is not None:
            try:
                move[key] = int(round(float(move[key])))
            except (TypeError, ValueError):
                return {"action": "stuck", "say": f"{key} is not a number", "see": move.get("see", "")}
    if action == "scroll" and move.get("amount") is None:
        move["amount"] = -5
    if action in ("hotkey", "press") and not isinstance(move.get("keys"), list):
        keys = move.get("keys") or move.get("text") or ""
        move["keys"] = [k.strip() for k in re.split(r"[+,\s]+", str(keys)) if k.strip()]
    move["say"] = str(move.get("say") or "")[:200]
    move["see"] = str(move.get("see") or "")[:200]
    return move


def same_move(a: dict, b: dict) -> bool:
    keys = ("action", "x", "y", "x2", "y2", "text", "keys", "amount")
    return all(a.get(k) == b.get(k) for k in keys)


def run(goal: str, look: Callable[[], tuple[bytes, tuple[int, int]]], decide: Callable[[str, bytes], str],
        act: Callable[[dict], None], *, steps: int = 20, pause: float = 1.0,
        stopped: Callable[[], bool] = lambda: False, report: Callable[[dict], None] | None = None) -> str:
    """Look, decide, act, up to ``steps`` times. ``look`` returns the screenshot (PNG bytes and its size);
    ``decide`` gets the prompt and the picture and returns the brain's text; ``act`` makes a parsed move.
    ``stopped`` is asked before every move ("спри"); ``report`` hears each move for the live view.
    Returns what happened, in a few lines."""
    report = report or (lambda _event: None)
    history: list[str] = []
    moves: list[dict] = []
    for n in range(1, max(1, int(steps)) + 1):
        if stopped():
            return "Спрях, както каза. " + _summary(history)
        image, (width, height) = look()
        shown = "".join(f"- {line}\n" for line in history[-6:])
        prompt = MOVE_PROMPT.format(goal=goal, width=width, height=height,
                                    history=f"Your moves so far:\n{shown}" if shown else "")
        try:
            move = parse_move(decide(prompt, image))
        except Exception as exc:  # the brain failed: the loop ends with what was done, not with a traceback
            log.warning("the screen brain failed: %s", exc)
            return f"Спрях на ход {n}: мозъкът не отговори ({type(exc).__name__}). " + _summary(history)
        line = f"{n}. {move['action']}: {move['say'] or move['see']}".strip(": ")
        report({"type": "step", "id": f"screen-{id(history)}-{n}", "agent": "Jarvis", "tool": "work_on_screen",
                "state": "running", "detail": line[:160]})
        if move["action"] == "done":
            report({"type": "step", "id": f"screen-{id(history)}-{n}", "state": "ok"})
            return f"Готово след {n - 1} хода: {move['say'] or move['see']}\n" + _summary(history)
        if move["action"] == "stuck":
            report({"type": "step", "id": f"screen-{id(history)}-{n}", "state": "error", "memory": {"result": move["say"]}})
            return f"Спрях на ход {n}: {move['say']}\n" + _summary(history)
        if len(moves) >= 2 and same_move(move, moves[-1]) and same_move(move, moves[-2]):
            report({"type": "step", "id": f"screen-{id(history)}-{n}", "state": "error",
                    "memory": {"result": "the same move three times"}})
            return f"Спрях на ход {n}: същият ход трети път ({move['action']}) не променя нищо.\n" + _summary(history)
        try:
            act(move)
            report({"type": "step", "id": f"screen-{id(history)}-{n}", "state": "ok"})
        except Exception as exc:
            report({"type": "step", "id": f"screen-{id(history)}-{n}", "state": "error", "memory": {"result": str(exc)}})
            history.append(f"{line} -> failed: {exc}")
            moves.append(move)
            continue
        history.append(line)
        moves.append(move)
        if pause:
            time.sleep(pause)
    return f"Направих {len(history)} хода и спрях (лимитът за една задача). Кажи „продължи“ за още.\n" + _summary(history)


def _summary(history: list[str]) -> str:
    return "Ходове:\n" + "\n".join(history[-10:]) if history else "Нямаше ходове."
