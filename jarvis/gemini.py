"""Google Gemini as Jarvis's brain: free within Google's limits, with a key from aistudio.google.com/apikey.

The tool loop in brain.py speaks the Claude Messages format. ``GeminiBrain.create`` takes the same
request, sends it to Gemini and returns the answer in that format. Gemini's own reply is kept with the
answer and sent back unchanged on the next step, because its function calls carry signed thoughts.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger("jarvis.gemini")

# Free models, most free requests a day first. Each has its own daily allowance (3.8 Flash only about
# 20 a day, the Flash-Lite models hundreds), so when one runs out Jarvis moves on to the next.
FREE_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"]
DEFAULT_MODEL = FREE_MODELS[0]
# Real work (the full agent, with tools) starts on the stronger Flash: Flash-Lite is quick and has the most free
# requests, so it keeps short answers, but with dozens of tools it often talks instead of acting. When Flash's
# free requests run out for the day, the others follow in FREE_MODELS order.
AGENT_MODEL = "gemini-3.5-flash"
THINKING = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
REFUSED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}
ONLY_FOR_GEMINI = {"google_search"}  # Claude searches with its own server tools


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def make_client():
    from google import genai
    from google.genai import types

    if not available():
        raise RuntimeError("GEMINI_API_KEY is not set. Get a free key at aistudio.google.com/apikey.")
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=180_000))


@dataclass
class Block:
    """A content block shaped like the Claude SDK's text and tool_use blocks."""

    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    call_id: str | None = None  # Gemini's own id for the call, if it gave one


class Reply(list):
    """The blocks of one answer, plus Gemini's original content (and the model that wrote it) to send
    back next time."""

    raw = None
    model = None


@dataclass
class Answer:
    content: Reply
    stop_reason: str
    model: str | None = None
    usage: dict | None = None  # tokens, for the app's "Разход" tab (see usage.py)


def _get(block, key, default=None):
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(_get(b, "text", "")) for b in content or [] if _get(b, "type") == "text")


def _images_of(content) -> list[tuple[str, bytes]]:
    if isinstance(content, str):
        return []
    out = []
    for b in content or []:
        source = _get(b, "source") or {}
        if _get(b, "type") == "image" and _get(source, "type") == "base64":
            out.append((_get(source, "media_type"), base64.b64decode(_get(source, "data"))))
    return out


def to_contents(messages: list, model: str | None = None) -> list:
    """Claude-format messages -> Gemini contents. Gemini's own replies go back as they came, unless
    another model wrote them (signed thoughts only work with the model that made them)."""
    from google.genai import types

    calls: dict[str, tuple[str, str | None]] = {}  # tool_use id -> (name, Gemini's call id)
    out = []
    for message in messages:
        content = message["content"]
        if message["role"] == "assistant":
            raw = getattr(content, "raw", None)
            if raw is not None and model not in (None, getattr(content, "model", model)):
                raw = None
            if raw is not None:
                out.append(raw)
                for b in content:
                    if b.type == "tool_use":
                        calls[b.id] = (b.name, b.call_id)
                continue
            # Text from history, or another model's blocks if the brain changed in the middle of a task:
            # its tool calls are written out as text, since only a model's own calls can be sent back to it.
            lines = []
            for b in [content] if isinstance(content, str) else content:
                kind = "text" if isinstance(b, str) else _get(b, "type")
                if kind == "text":
                    lines.append(b if isinstance(b, str) else _get(b, "text", ""))
                elif kind == "tool_use":
                    calls[_get(b, "id")] = (_get(b, "name"), "")
                    lines.append(f"[I used the tool {_get(b, 'name')} with {json.dumps(_get(b, 'input') or {}, ensure_ascii=False)}]")
            if any(line.strip() for line in lines):
                out.append(types.Content(role="model", parts=[types.Part(text="\n".join(lines))]))
            continue

        parts = []
        for b in [content] if isinstance(content, str) else content:
            kind = "text" if isinstance(b, str) else _get(b, "type")
            if kind == "text":
                parts.append(types.Part(text=b if isinstance(b, str) else _get(b, "text", "")))
            elif kind == "image":
                for mime, data in _images_of([b]):
                    parts.append(types.Part.from_bytes(data=data, mime_type=mime))
            elif kind == "tool_result":
                name, call_id = calls.get(_get(b, "tool_use_id"), ("tool", None))
                result = _text_of(_get(b, "content"))
                if call_id == "":  # a call Claude made: answer it as text too
                    parts.append(types.Part(text=f"[Result of {name}: {result}]"))
                    continue
                pictures = [types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(mime_type=m, data=d))
                            for m, d in _images_of(_get(b, "content"))]
                parts.append(types.Part(function_response=types.FunctionResponse(
                    id=call_id, name=name, response={"error" if _get(b, "is_error") else "result": result},
                    parts=pictures or None)))
        if parts:
            out.append(types.Content(role="user", parts=parts))
    return out


def for_claude(messages: list) -> list:
    """The same conversation with Gemini's blocks turned into plain dicts, so Claude can take over
    in the middle of a turn (e.g. after "switch to Haiku"); the SDK cannot send Block objects."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, Reply) or (isinstance(content, list) and any(isinstance(b, Block) for b in content)):
            blocks = []
            for b in content:
                if not isinstance(b, Block):
                    blocks.append(b)
                elif b.type == "tool_use":
                    blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input or {})})
                elif b.text.strip():
                    blocks.append({"type": "text", "text": b.text})
            m = {**m, "content": blocks or [{"type": "text", "text": "…"}]}
        out.append(m)
    return out


def to_declarations(tools: list) -> list:
    from google.genai import types

    return [types.FunctionDeclaration(name=t["name"], description=t.get("description", ""),
                                      parameters_json_schema=t.get("input_schema") or {"type": "object", "properties": {}})
            for t in tools if "input_schema" in t]  # server tools (Claude's web search) have no schema


def usage_of(response) -> dict:
    meta = getattr(response, "usage_metadata", None)
    get = (lambda k: int(getattr(meta, k, 0) or 0)) if meta is not None else (lambda k: 0)
    cached = get("cached_content_token_count")
    return {"input": get("prompt_token_count") - cached, "cache_read": cached, "cache_write": 0,
            "output": get("candidates_token_count") + get("thoughts_token_count")}


def from_response(response, model: str | None = None) -> Answer:
    """Gemini's answer in Claude's shape. stop_reason "empty": nothing usable came back (a broken tool call,
    MALFORMED_FUNCTION_CALL, or no parts), so GeminiBrain.create asks again."""
    candidate = (response.candidates or [None])[0]
    if candidate is None or candidate.content is None:
        blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
        reason = str(getattr(getattr(candidate, "finish_reason", None), "name", "") or "")
        return Answer(Reply(), "refusal" if blocked or reason in REFUSED else "empty", model, usage_of(response))
    blocks = Reply()
    blocks.raw, blocks.model = candidate.content, model
    for part in candidate.content.parts or []:
        if part.thought:
            continue
        if part.function_call:
            call = part.function_call
            blocks.append(Block("tool_use", id=call.id or f"call_{uuid.uuid4().hex[:12]}", name=call.name,
                                input=dict(call.args or {}), call_id=call.id))
        elif part.text:
            blocks.append(Block("text", text=part.text))
    reason = str(getattr(candidate.finish_reason, "name", candidate.finish_reason) or "")
    if any(b.type == "tool_use" for b in blocks):
        stop = "tool_use"
    elif reason in REFUSED:
        stop = "refusal"
    elif reason == "MAX_TOKENS":
        stop = "max_tokens"
    elif not blocks:
        stop = "empty"
    else:
        stop = "end_turn"
    return Answer(blocks, stop, model, usage_of(response))


class UsedUp(RuntimeError):
    """Every free Gemini model has used up its requests for today."""


class Busy(RuntimeError):
    """Gemini's free models are overloaded or too slow right now (the next free brain answers)."""


def timed_out(exc: Exception) -> bool:
    return "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()


class GeminiBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with Gemini."""

    def __init__(self, client=None, wait=time.sleep):
        self._client = client
        self._wait = wait
        self.spent: dict[str, float] = {}  # model -> when its free daily requests come back
        self.resting: dict[str, float] = {}  # model -> when it may be asked again after being busy or too slow

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str):
        from google.genai import errors, types

        if not isinstance(system, str):
            system = "\n\n".join(_get(b, "text", "") for b in system)
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=to_declarations(tools))] if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=THINKING.get(effort, "medium")),
            max_output_tokens=max_tokens,
        )
        busy = empty = None
        for current in self.models(model, agent=bool(tools)):
            contents = to_contents(messages, current)
            for attempt in range(3):
                try:
                    response = self.client.models.generate_content(model=current, contents=contents, config=config)
                except errors.APIError as exc:
                    if exc.code == 429 and daily_limit(exc):
                        log.warning("free requests for today are used up on %s; trying the next model", current)
                        self.spent[current] = next_reset()
                        break
                    if exc.code == 404 and current != model:  # a fallback model this key cannot use: skip it today
                        log.warning("Gemini has no %s for this key, trying the next model: %s", current, exc)
                        self.spent[current] = next_reset()
                        break
                    if exc.code not in (429, 500, 502, 503, 504):
                        raise
                    if exc.code == 504 or attempt == 2:  # too slow (DEADLINE_EXCEEDED) or still busy: the next one
                        log.warning("Gemini %s on %s, trying the next model: %s", exc.code, current, exc)
                        self.resting[current], busy = time.time() + 120, exc
                        break
                    # A few requests a minute are allowed: wait as long as Google says, then try again.
                    log.warning("Gemini %s, retrying: %s", exc.code, exc)
                    self._wait(retry_after(exc, 5 * 2 ** attempt))
                    continue
                except Exception as exc:
                    if not timed_out(exc):
                        raise
                    log.warning("Gemini did not answer in time on %s, trying the next model: %s", current, exc)
                    self.resting[current], busy = time.time() + 120, exc
                    break
                answer = from_response(response, current)
                if answer.stop_reason != "empty":
                    return answer
                # A broken tool call (MALFORMED_FUNCTION_CALL) or no answer at all: once more, then the next model.
                log.warning("Gemini %s gave an empty or broken answer (try %d)", current, attempt + 1)
                empty = answer
                if attempt:
                    break
        if empty is not None:
            empty.stop_reason = "end_turn"
            return empty
        if busy is not None:
            raise Busy(f"Gemini is overloaded or too slow right now: {busy}")
        raise UsedUp("The free Gemini requests for today are used up on every free model.")

    def models(self, model: str, agent: bool = False) -> list[str]:
        """The model to use and the free ones to fall back to, minus those used up for today or busy for a moment.
        Real work (``agent``: a request with tools) on the default model starts on AGENT_MODEL."""
        now = time.time()
        first = [AGENT_MODEL, model] if agent and model == DEFAULT_MODEL else [model]
        return [m for m in dict.fromkeys([*first, *FREE_MODELS])
                if self.spent.get(m, 0) <= now and self.resting.get(m, 0) <= now]


def daily_limit(exc) -> bool:
    """True when the free requests for the whole day are gone (not just for this minute)."""
    return "perday" in str(exc).lower().replace(" ", "").replace("_", "")


def retry_after(exc, default: float) -> float:
    found = re.search(r"retry(?:Delay)?\D{0,6}(\d+(?:\.\d+)?)s", str(exc), re.I)
    return min(60.0, float(found[1])) if found else default


def next_reset() -> float:
    """Google renews free requests at midnight Pacific time (about 07:00 UTC)."""
    now = datetime.now(timezone.utc)
    reset = now.replace(hour=7, minute=0, second=0, microsecond=0)
    return (reset if reset > now else reset + timedelta(days=1)).timestamp()


def search(query: str, model: str | None = None, client=None) -> str:
    """One Google search through Gemini, answered with sources."""
    from google.genai import types

    client = client or make_client()
    response = client.models.generate_content(
        model=model if model and model.startswith("gemini") else DEFAULT_MODEL,
        contents=f"Search Google and answer with the facts you find (keep numbers, dates, prices and names): {query}",
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],
                                           thinking_config=types.ThinkingConfig(thinking_level="low")),
    )
    text = response.text or "Нищо не намерих."
    meta = response.candidates[0].grounding_metadata if response.candidates else None
    sources = [f"- {c.web.title}: {c.web.uri}" for c in (getattr(meta, "grounding_chunks", None) or []) if c.web]
    return text + ("\n\nSources:\n" + "\n".join(sources[:8]) if sources else "")


WATCH_PROMPT = ("Watch the whole video, including the sound, and answer in Bulgarian. {question}\n"
                "Give timestamps (mm:ss), quote what is said and the text on screen, and say where you are not sure.")


def watch(source: str, question: str = "", client=None, wait=time.sleep) -> str:
    """Gemini watches a video (a file on this computer or a YouTube link) and answers about it."""
    from google.genai import errors, types

    client = client or make_client()
    question = question or "Describe in detail everything that happens in it."
    uploaded = None
    if re.match(r"https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", source):
        video = types.Part(file_data=types.FileData(file_uri=source, mime_type="video/*"))
    else:
        uploaded = client.files.upload(file=source)
        while uploaded.state and uploaded.state.name == "PROCESSING":
            wait(5)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state and uploaded.state.name != "ACTIVE":
            raise RuntimeError(f"Google could not process the video: {uploaded.error or uploaded.state}")
        video = uploaded
    try:
        last = None
        # the best free models first; a long video may only fit at low detail
        for model in ["gemini-3.8-flash", "gemini-3.5-flash", *FREE_MODELS]:
            for detail in (None, types.MediaResolution.MEDIA_RESOLUTION_LOW):
                try:
                    answer = client.models.generate_content(
                        model=model, contents=[video, WATCH_PROMPT.format(question=question)],
                        config=types.GenerateContentConfig(media_resolution=detail, max_output_tokens=16000))
                    if answer.text:
                        return answer.text
                except errors.APIError as exc:
                    last = exc
                    if exc.code == 429 and daily_limit(exc):
                        break  # this model is used up for today; the next one
        raise last or RuntimeError("Gemini gave no answer about the video.")
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
