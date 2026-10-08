"""A brain on this computer (Ollama): free, with no daily limit, and it works without internet.

Install it once (Windows PowerShell):  winget install Ollama.Ollama   then   ollama pull qwen3:4b
Jarvis finds it by itself and uses it when the free Gemini requests for the day are used up, when Claude's
daily spending cap is reached, or always with JARVIS_MODEL=local. It is slower and simpler than the cloud
brains, so it gets the most useful tools only (LOCAL_TOOLS) to keep its prompt small.
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

from .gemini import Answer, Block, Reply, _get, _images_of, _text_of

log = logging.getLogger("jarvis.local")

# Models that answer well in Bulgarian and can use tools, best first; any other installed model also works.
PREFERRED = ["qwen3", "gemma3", "llama3.2", "llama3.1", "mistral", "phi4"]
LOCAL_TOOLS = {
    "remember", "recall", "find_contact", "add_task", "list_tasks", "complete_task", "add_reminder",
    "list_reminders", "list_events", "open_target", "list_dir", "read_file", "find_files", "system_status",
    "media_control", "set_volume", "lock_computer", "play_youtube", "weather", "android", "phone_call", "phone_sms",
    "request_approval", "run_shell", "vault_search", "vault_note",
}
_seen: dict = {"at": 0.0, "models": []}


def url() -> str:
    return (os.environ.get("JARVIS_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


def is_local(model: str | None) -> bool:
    return bool(model) and (model == "local" or model.startswith("local:"))


def models(timeout: float = 0.8) -> list[str]:
    """The models Ollama has on this computer ([] when it is not running); checked at most once a minute."""
    if time.time() - _seen["at"] < 60:
        return _seen["models"]
    try:
        with urllib.request.urlopen(url() + "/api/tags", timeout=timeout) as resp:
            names = [m["name"] for m in json.loads(resp.read()).get("models", [])]
    except Exception:
        names = []
    _seen.update(at=time.time(), models=names)
    return names


def available() -> bool:
    return bool(models())


def pick(wanted: str | None = None) -> str | None:
    """The installed model to use: the one asked for (JARVIS_LOCAL_MODEL), else the best known one."""
    names = models()
    if not names:
        return None
    if wanted:
        return next((n for n in names if n == wanted or n.split(":")[0] == wanted), wanted)
    for family in PREFERRED:
        found = next((n for n in names if n.split(":")[0] == family), None)
        if found:
            return found
    return names[0]


def to_messages(system: str, messages: list) -> list[dict]:
    """Claude-format messages -> Ollama chat messages."""
    out = [{"role": "system", "content": system}]
    names: dict[str, str] = {}  # tool_use id -> tool name
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
                    names[_get(b, "id")] = _get(b, "name")
                    calls.append({"function": {"name": _get(b, "name"), "arguments": dict(_get(b, "input") or {})}})
            entry = {"role": "assistant", "content": "\n".join(t for t in text if t)}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        text, images = [], []
        for b in blocks:
            kind = "text" if isinstance(b, str) else _get(b, "type")
            if kind == "text":
                text.append(b if isinstance(b, str) else _get(b, "text", ""))
            elif kind == "image":
                import base64

                images += [base64.b64encode(data).decode() for _mime, data in _images_of([b])]
            elif kind == "tool_result":
                result = _text_of(_get(b, "content"))
                out.append({"role": "tool", "tool_name": names.get(_get(b, "tool_use_id"), "tool"),
                            "content": ("ERROR: " if _get(b, "is_error") else "") + result})
        if text or images:
            entry = {"role": "user", "content": "\n".join(t for t in text if t)}
            if images:
                entry["images"] = images
            out.append(entry)
    return out


def to_tools(tools: list) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
            for t in tools if "input_schema" in t and t["name"] in LOCAL_TOOLS]


def from_reply(data: dict, model: str) -> Answer:
    message = data.get("message") or {}
    blocks = Reply()
    blocks.model = model
    text = re.sub(r"<think>.*?</think>", "", message.get("content") or "", flags=re.S).strip()
    if text:
        blocks.append(Block("text", text=text))
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        blocks.append(Block("tool_use", id=f"call_{uuid.uuid4().hex[:12]}", name=fn.get("name", ""), input=args))
    stop = ("tool_use" if any(b.type == "tool_use" for b in blocks)
            else "max_tokens" if data.get("done_reason") == "length" else "end_turn")
    usage = {"input": int(data.get("prompt_eval_count") or 0), "output": int(data.get("eval_count") or 0),
             "cache_read": 0, "cache_write": 0}
    return Answer(blocks, stop, "local:" + model, usage)


class LocalBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with a model running in Ollama."""

    def __init__(self, post=None):
        self._post = post or _post

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str) -> Answer:
        if not isinstance(system, str):
            system = "\n\n".join(_get(b, "text", "") for b in system)
        body = {
            "model": model, "stream": False, "messages": to_messages(system, messages),
            # The default context is too short for Jarvis's prompt and tools; this fits them on an ordinary laptop.
            "options": {"num_predict": min(max_tokens, 4096), "num_ctx": 8192 if tools else 4096},
            "think": effort == "max",  # thinking makes a laptop model much slower
        }
        declared = to_tools(tools)
        if declared:
            body["tools"] = declared
        for _ in range(3):
            try:
                return from_reply(self._post("/api/chat", body), model)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else str(exc)
                # Not every model can think or use tools: ask again without what it can't do.
                if "think" in detail and "think" in body:
                    body.pop("think")
                elif "tools" in detail and "tools" in body:
                    body.pop("tools")
                else:
                    raise RuntimeError(f"The local brain (Ollama, {model}) failed: {detail}") from exc
        raise RuntimeError(f"The local brain (Ollama, {model}) did not answer.")


def _post(path: str, body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url() + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
