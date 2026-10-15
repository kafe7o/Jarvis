"""Groq as a brain: free (no card needed) with a key from console.groq.com/keys, and very fast.

The idea comes from a video on what a Jarvis costs: Groq's free tier answers in a blink, so it takes the
quick lane (router level 2) and stands in when Gemini's free requests run out or Claude's daily cap is
reached; JARVIS_MODEL=groq makes it the main brain. Its free tier allows only about 8000 tokens a minute
per model, so the full agent gets the most useful tools only (local.LOCAL_TOOLS) and the recent part of
the conversation; when one model is busy or used up for the day, the next free one answers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid

from .gemini import Answer, Block, Reply, _get, _text_of, brain_note, written_call
from .local import LOCAL_TOOLS
from .tools import normalize_schema


log = logging.getLogger("jarvis.groq")

URL = "https://api.groq.com/openai/v1/chat/completions"
# Free models, best first; each has its own allowance a minute and a day. One that Groq has retired answers 404
# and rests for a day while the next one answers. Llama 4 Scout allows more tokens a minute than the others.
FREE_MODELS = ["openai/gpt-oss-120b", "meta-llama/llama-4-scout-17b-16e-instruct", "openai/gpt-oss-20b",
               "llama-3.1-8b-instant"]
DEFAULT_MODEL = "groq"
# The tools Groq gets (its prompt must stay small): the everyday ones, plus the screen, the browser and search, so
# it can carry the same work as Gemini when it is the brain the owner picked or the one standing in.
TOOLS = LOCAL_TOOLS | {"read_window", "click_text", "look_at_screen", "web_browser", "google_search"}
REASONING = {"low": "low", "medium": "medium", "high": "medium", "xhigh": "medium", "max": "high"}
BUDGET = 22000  # characters of prompt (system, tools and conversation) that fit the free tier's minute


def available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def is_groq(model: str | None) -> bool:
    return bool(model) and (model == "groq" or model.startswith("groq:"))


class UsedUp(RuntimeError):
    """Every free Groq model is busy or used up for now."""


NO_EYES = "(An image was shared here; this brain cannot see images.)"


def to_messages(system: str, messages: list) -> list[dict]:
    """Claude-format messages -> OpenAI-style chat messages (what Groq speaks)."""
    out = [{"role": "system", "content": system}]
    for message in messages:
        content = message["content"]
        blocks = [content] if isinstance(content, str) else list(content)
        if message["role"] == "assistant":
            text, calls = [], []
            for b in blocks:
                kind = "text" if isinstance(b, str) else _get(b, "type")
                if kind == "text":
                    text.append(b if isinstance(b, str) else _get(b, "text", ""))
                elif kind == "tool_use":
                    calls.append({"id": _get(b, "id"), "type": "function", "function": {
                        "name": _get(b, "name"), "arguments": json.dumps(dict(_get(b, "input") or {}), ensure_ascii=False)}})
            entry = {"role": "assistant", "content": "\n".join(t for t in text if t)}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        text = []
        for b in blocks:
            kind = "text" if isinstance(b, str) else _get(b, "type")
            if kind == "text":
                text.append(b if isinstance(b, str) else _get(b, "text", ""))
            elif kind == "image":
                text.append(NO_EYES)
            elif kind == "tool_result":
                result = _text_of(_get(b, "content")) or "(done)"
                out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"),
                            "content": ("ERROR: " if _get(b, "is_error") else "") + result[:6000]})
        if text:
            out.append({"role": "user", "content": "\n".join(t for t in text if t)})
    return out


CUT = 1500  # characters an old tool result keeps when the current task alone is too big for the room


def trim(chat: list[dict], room: int) -> list[dict]:
    """Keep the system prompt and as much of the end of the conversation as fits ``room`` characters, starting
    at one of the person's own messages so every tool call keeps its result. When the current task alone (from
    the person's last message) is too big, its oldest tool results are cut to CUT characters instead of dropping
    messages: the laptop's mission, 00:32, sent Mistral a tool result right after the system prompt and got
    "Unexpected role 'tool' after role 'system'" from every model."""
    size = lambda m: len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "", ensure_ascii=False))
    system, rest = chat[0], chat[1:]
    starts = [i for i, m in enumerate(rest) if m["role"] == "user"] or [0]
    for start in starts:
        if sum(size(m) for m in rest[start:]) <= room:
            return [system, *rest[start:]]
    kept = [dict(m) for m in rest[starts[-1]:]]
    total = sum(size(m) for m in kept)
    for m in kept:  # the oldest results first; the newest keeps as much as possible
        if total <= room:
            break
        if m["role"] == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > CUT:
            total -= len(m["content"]) - CUT - 8
            m["content"] = m["content"][:CUT] + " …(cut)"
    return [system, *kept]



def to_tools(tools: list, only: set | None = LOCAL_TOOLS) -> list[dict]:
    """Claude-format tools -> OpenAI-style functions; ``only`` keeps just those (None = every tool)."""
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": normalize_schema(t.get("input_schema"))}}
            for t in tools if "input_schema" in t and (only is None or t["name"] in only)]


def from_reply(data: dict, model: str, prefix: str = "groq") -> Answer:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks = Reply()
    blocks.model = f"{prefix}:{model}"
    text = re.sub(r"<think>.*?</think>", "", message.get("content") or "", flags=re.S).strip()
    written = written_call(text) if not message.get("tool_calls") else None
    if written:  # the call was written out instead of made: it is made
        blocks.append(Block("tool_use", id=f"call_{uuid.uuid4().hex[:12]}", name=written[0], input=written[1]))
        text = ""
    if text:
        blocks.append(Block("text", text=text))
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = fn.get("arguments") or "{}"
            args = args if isinstance(args, dict) else json.loads(args)
        except (TypeError, ValueError):
            args = {}
        blocks.append(Block("tool_use", id=call.get("id") or f"call_{uuid.uuid4().hex[:12]}", name=fn.get("name", ""),
                            input=args if isinstance(args, dict) else {}))
    stop = ("tool_use" if any(b.type == "tool_use" for b in blocks)
            else "max_tokens" if choice.get("finish_reason") == "length" else "end_turn")
    used = data.get("usage") or {}
    usage = {"input": int(used.get("prompt_tokens") or 0), "output": int(used.get("completion_tokens") or 0),
             "cache_read": 0, "cache_write": 0}
    return Answer(blocks, stop, blocks.model, usage)


class GroqBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with Groq's free models."""

    def __init__(self, post=None, wait=time.sleep):
        self._post = post or _post
        self._wait = wait
        self.resting: dict[str, float] = {}  # model -> when it may be asked again
        self.fits: dict[str, int] = {}  # model -> characters of prompt that fit its tokens a minute

    def models(self, model: str) -> list[str]:
        chosen = model.partition(":")[2] or os.environ.get("JARVIS_GROQ_MODEL") or FREE_MODELS[0]
        now = time.time()
        return [m for m in dict.fromkeys([chosen, *FREE_MODELS]) if self.resting.get(m, 0) <= now]

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str) -> Answer:
        if not available():
            raise RuntimeError("GROQ_API_KEY is not set. Get a free key at console.groq.com/keys.")
        if not isinstance(system, str):
            system = "\n\n".join(_get(b, "text", "") for b in system)
        declared = to_tools(tools, TOOLS)
        for current in self.models(model):
            budget, cap, broken = self.fits.get(current, BUDGET), (2048 if declared else 1500), False
            for attempt in range(3):
                body = {"model": current, "messages": fit(system + brain_note("Groq", current), messages, declared, budget),
                        "max_tokens": min(max_tokens, cap)}
                if declared:
                    body.update(tools=declared, tool_choice="auto")
                if current.startswith("openai/gpt-oss"):
                    body["reasoning_effort"] = REASONING.get(effort, "medium")
                try:
                    return from_reply(self._post(body), current)
                except urllib.error.HTTPError as exc:
                    detail = _detail(exc)
                    if exc.code == 401:
                        raise RuntimeError("The Groq key is wrong. Get a new one at console.groq.com/keys.") from exc
                    if exc.code == 429 and "minute" in detail and attempt == 0:
                        self._wait(min(_retry_after(exc), 20))  # a few requests a minute: wait a moment
                        continue
                    smaller = shrink(detail) if exc.code == 413 else None
                    if smaller and attempt < 2:
                        # Too big for this model's tokens a minute (the answer's room counts too): a shorter
                        # prompt and answer, remembered for this model, instead of giving up on Groq.
                        budget, cap = int(budget * smaller), min(cap, 1024)
                        self.fits[current] = budget
                        log.warning("Groq: the request is too big for %s; trying it shorter", current)
                        continue
                    if exc.code in (404, 410) or (exc.code == 400 and "decommission" in detail):
                        log.warning("Groq has no model %s now; trying the next one", current)
                        self.resting[current] = time.time() + 86400
                        break
                    if exc.code in (413, 429, 500, 502, 503) or (exc.code == 400 and "tool_use_failed" in detail
                                                                   and broken):
                        rest = 3600 if exc.code == 429 and ("day" in detail or "TPD" in detail or "RPD" in detail) else 60
                        log.warning("Groq %s on %s, trying the next model: %s", exc.code, current, detail[:200])
                        self.resting[current] = time.time() + rest
                        break
                    if exc.code == 400 and "tool_use_failed" in detail:
                        broken = True
                        continue  # the model wrote a broken tool call: once more
                    raise RuntimeError(f"Groq did not answer ({exc.code}): {detail[:300]}") from exc
        raise UsedUp("Every free Groq model is busy or used up for now.")


def fit(system: str, messages: list, declared: list, budget: int) -> list[dict]:
    """The chat for one request within ``budget`` characters: the system prompt and as much of the end of the
    conversation as fits. A system prompt too long for this brain loses its middle (the persona's details, the
    lessons), never its start (who Jarvis is, the owner's standing order) or its end (what Jarvis remembers about
    the owner, the time): the owner's instructions hold on every brain."""
    keep = int(budget * 0.6)
    if len(system) > keep:
        head, tail = int(keep * 0.4), int(keep * 0.6)
        system = system[:head] + "\n…(some instructions are left out to fit this brain)…\n" + system[-tail:]
    room = budget - len(system) - len(json.dumps(declared, ensure_ascii=False))
    return trim(to_messages(system, messages), max(room, 2000))


def shrink(detail: str) -> float | None:
    """How much smaller a request must be, from Groq's 413 "Limit 8000, Requested 11234" (None if it does not say)."""
    found = re.search(r"Limit\D{0,3}(\d+)\D+Requested\D{0,3}(\d+)", detail)
    if not found or int(found.group(2)) <= int(found.group(1)):
        return None
    return max(0.3, int(found.group(1)) / int(found.group(2)) * 0.85)


def _detail(exc) -> str:
    try:
        return exc.read().decode("utf-8", "replace")
    except Exception:
        return str(exc)


def _retry_after(exc) -> float:
    try:
        return float(exc.headers.get("retry-after") or 5)
    except (AttributeError, TypeError, ValueError):
        return 5.0


def _post(body: dict, timeout: float = 120) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
        "User-Agent": "jarvis"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
