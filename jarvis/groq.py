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

from .gemini import Answer, Block, Reply, _get, _text_of
from .local import LOCAL_TOOLS

log = logging.getLogger("jarvis.groq")

URL = "https://api.groq.com/openai/v1/chat/completions"
# Free models, best first; each has its own allowance a minute and a day.
FREE_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
DEFAULT_MODEL = "groq"
REASONING = {"low": "low", "medium": "medium", "high": "medium", "xhigh": "medium", "max": "high"}
BUDGET = 22000  # characters of prompt (system, tools and conversation) that fit the free tier's minute


def available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def is_groq(model: str | None) -> bool:
    return bool(model) and (model == "groq" or model.startswith("groq:"))


class UsedUp(RuntimeError):
    """Every free Groq model is busy or used up for now."""


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
                text.append("(An image was shared here; this brain cannot see images.)")
            elif kind == "tool_result":
                result = _text_of(_get(b, "content")) or "(done)"
                out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"),
                            "content": ("ERROR: " if _get(b, "is_error") else "") + result[:6000]})
        if text:
            out.append({"role": "user", "content": "\n".join(t for t in text if t)})
    return out


def trim(chat: list[dict], room: int) -> list[dict]:
    """Keep the system prompt and as much of the end of the conversation as fits ``room`` characters,
    starting at one of the person's own messages so every tool call keeps its result."""
    size = lambda m: len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "", ensure_ascii=False))
    system, rest = chat[0], chat[1:]
    total = sum(size(m) for m in rest)
    start = 0
    while total > room and start < len(rest) - 1:
        total -= size(rest[start])
        start += 1
        while start < len(rest) - 1 and rest[start]["role"] != "user":
            total -= size(rest[start])
            start += 1
    return [system, *rest[start:]]


def to_tools(tools: list) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools if "input_schema" in t and t["name"] in LOCAL_TOOLS]


def from_reply(data: dict, model: str) -> Answer:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks = Reply()
    blocks.model = "groq:" + model
    text = re.sub(r"<think>.*?</think>", "", message.get("content") or "", flags=re.S).strip()
    if text:
        blocks.append(Block("text", text=text))
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        blocks.append(Block("tool_use", id=call.get("id") or f"call_{uuid.uuid4().hex[:12]}", name=fn.get("name", ""),
                            input=args if isinstance(args, dict) else {}))
    stop = ("tool_use" if any(b.type == "tool_use" for b in blocks)
            else "max_tokens" if choice.get("finish_reason") == "length" else "end_turn")
    used = data.get("usage") or {}
    usage = {"input": int(used.get("prompt_tokens") or 0), "output": int(used.get("completion_tokens") or 0),
             "cache_read": 0, "cache_write": 0}
    return Answer(blocks, stop, "groq:" + model, usage)


class GroqBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with Groq's free models."""

    def __init__(self, post=None, wait=time.sleep):
        self._post = post or _post
        self._wait = wait
        self.resting: dict[str, float] = {}  # model -> when it may be asked again

    def models(self, model: str) -> list[str]:
        chosen = model.partition(":")[2] or os.environ.get("JARVIS_GROQ_MODEL") or FREE_MODELS[0]
        now = time.time()
        return [m for m in dict.fromkeys([chosen, *FREE_MODELS]) if self.resting.get(m, 0) <= now]

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str) -> Answer:
        if not available():
            raise RuntimeError("GROQ_API_KEY is not set. Get a free key at console.groq.com/keys.")
        if not isinstance(system, str):
            system = "\n\n".join(_get(b, "text", "") for b in system)
        declared = to_tools(tools)
        room = BUDGET - len(system) - len(json.dumps(declared, ensure_ascii=False))
        chat = trim(to_messages(system, messages), max(room, 4000))
        for current in self.models(model):
            body = {"model": current, "messages": chat, "max_tokens": min(max_tokens, 2048 if declared else 1500)}
            if declared:
                body.update(tools=declared, tool_choice="auto")
            if current.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = REASONING.get(effort, "medium")
            for attempt in range(2):
                try:
                    return from_reply(self._post(body), current)
                except urllib.error.HTTPError as exc:
                    detail = _detail(exc)
                    if exc.code == 401:
                        raise RuntimeError("The Groq key is wrong. Get a new one at console.groq.com/keys.") from exc
                    if exc.code == 429 and "minute" in detail and attempt == 0:
                        self._wait(min(_retry_after(exc), 20))  # a few requests a minute: wait a moment
                        continue
                    if exc.code in (413, 429, 500, 502, 503) or (exc.code == 400 and "tool_use_failed" in detail
                                                                   and attempt == 1):
                        rest = 3600 if exc.code == 429 and ("day" in detail or "TPD" in detail or "RPD" in detail) else 60
                        log.warning("Groq %s on %s, trying the next model: %s", exc.code, current, detail[:200])
                        self.resting[current] = time.time() + rest
                        break
                    if exc.code == 400 and "tool_use_failed" in detail:
                        continue  # the model wrote a broken tool call: once more
                    raise RuntimeError(f"Groq did not answer ({exc.code}): {detail[:300]}") from exc
        raise UsedUp("Every free Groq model is busy or used up for now.")


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
