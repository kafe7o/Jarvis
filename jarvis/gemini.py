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
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.gemini")

DEFAULT_MODEL = "gemini-3.8-flash"
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
    """The blocks of one answer, plus Gemini's original content to send back next time."""

    raw = None


@dataclass
class Answer:
    content: Reply
    stop_reason: str


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


def to_contents(messages: list) -> list:
    """Claude-format messages -> Gemini contents."""
    from google.genai import types

    calls: dict[str, tuple[str, str | None]] = {}  # tool_use id -> (name, Gemini's call id)
    out = []
    for message in messages:
        content = message["content"]
        if message["role"] == "assistant":
            raw = getattr(content, "raw", None)
            if raw is not None:
                out.append(raw)
                for b in content:
                    if b.type == "tool_use":
                        calls[b.id] = (b.name, b.call_id)
                continue
            # Text from history, or Claude's blocks if the brain changed in the middle of a task:
            # its tool calls are written out as text, since only Gemini's own calls can be sent back.
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


def to_declarations(tools: list) -> list:
    from google.genai import types

    return [types.FunctionDeclaration(name=t["name"], description=t.get("description", ""),
                                      parameters_json_schema=t.get("input_schema") or {"type": "object", "properties": {}})
            for t in tools if "input_schema" in t]  # server tools (Claude's web search) have no schema


def from_response(response) -> Answer:
    candidate = (response.candidates or [None])[0]
    if candidate is None or candidate.content is None:
        blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
        return Answer(Reply(), "refusal" if blocked else "end_turn")
    blocks = Reply()
    blocks.raw = candidate.content
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
    else:
        stop = "end_turn"
    return Answer(blocks, stop)


class GeminiBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with Gemini."""

    def __init__(self, client=None, wait=time.sleep):
        self._client = client
        self._wait = wait

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
        contents = to_contents(messages)
        for attempt in range(4):
            try:
                return from_response(self.client.models.generate_content(model=model, contents=contents, config=config))
            except errors.APIError as exc:
                # The free tier allows a few requests a minute: wait and try again before giving up.
                if exc.code not in (429, 500, 503) or attempt == 3:
                    raise
                log.warning("Gemini %s, retrying: %s", exc.code, exc)
                self._wait(5 * 2 ** attempt)


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
