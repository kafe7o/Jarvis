"""Jarvis's brain: models running on this computer in Ollama. Free, with no daily limit, no keys, and it works
without internet; nothing the owner says leaves the laptop.

Settings > Мозък installs Ollama and downloads the models (localmodels.py); a .gguf file in Documents/Jarvis Models
(or Downloads) is added with one button. The best installed model does the work (Qwen 3.5 9B first), the small one
beside it (4B) the plain answers; JARVIS_LOCAL_MODEL names another.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import uuid

from .answers import KEEP_FULL, Answer, Block, Reply, _get, _images_of, _text_of, shorten, written_call
from .tools import normalize_schema

log = logging.getLogger("jarvis.local")

# Models that answer well in Bulgarian and can use tools, best first; any other installed model also works.
PREFERRED = ["huihui_ai/qwen3.5-abliterated", "qwen3.5", "qwen3", "gemma3", "llama3.2", "llama3.1", "mistral", "phi4"]
# Told to a laptop model on top of Jarvis's prompt: a small model keeps to these better than to long prose.
SMALL_RULES = """

Rules for you, the brain running on this laptop (follow them exactly):
1. To do something, CALL a tool. Never write a tool call, JSON or code as your answer, and never say you did \
something you did not call a tool for.
2. One step at a time: call the tool, read its result, then decide the next step. If a tool fails, read the error \
and try a different way; do not repeat the same call.
3. When the task is done, check the result (read it back or look at the screen) before you say it is done.
4. Answer briefly in the owner's language. If you do not know, say so; do not invent facts, names or numbers."""
# Qwen's own settings for agent work (thinking or not).
OPTIONS = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0}
# How much the model reads at once. Ollama's default silently cut Jarvis's prompt and tools, so the model forgot the
# task; his prompt and tools alone are about 13,000 tokens. One size for every step: a new size reloads the model.
# Qwen 3.5 keeps most of its context cheaply (about 1 GB for 32,000 tokens on the 9B).
NUM_CTX = int(os.environ.get("JARVIS_LOCAL_CTX") or 32768)
KEEP_LOADED = "30m"  # loading a 9B model into the graphics card takes seconds: it stays there between steps
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


NOT_READY = ("Мозъкът на лаптопа (Ollama) не работи или няма изтеглен модел. Отвори Настройки > Мозък: там се "
             "инсталира Ollama и се тегли модел.")


def pick(wanted: str | None = None) -> str | None:
    """The installed model to use: the one asked for (JARVIS_LOCAL_MODEL), else the best known one (a 9B, Qwen 3.5
    first, before the 4B and the others; never the small model that only finds memories by meaning)."""
    names = [n for n in models() if "embed" not in n.lower()]
    if not names:
        return None
    if wanted:
        return next((n for n in names if n == wanted or n.split(":")[0] == wanted), wanted)
    rank = lambda n: (not re.search(r"(?<![\d.])9b", n, re.I), not QWEN35.search(n),
                      next((i for i, f in enumerate(PREFERRED) if n.split(":")[0].endswith(f)), len(PREFERRED)))
    return min(names, key=rank)


QWEN35 = re.compile(r"qwen[-_]?3[._]?5", re.I)


def quick_partner(name: str | None) -> str | None:
    """The smaller model of the same family installed next to ``name`` (4B beside 9B), for the easy steps; None
    when there is none."""
    if not name or not re.search(r"(?<![\d.])9b", name, re.I):
        return None
    wanted = re.sub(r"(?<![\d.])9b", "4b", name, flags=re.I).lower()
    names = models()
    sibling = next((n for n in names if n.lower() in (wanted, wanted + ":latest")), None)
    if sibling:
        return sibling
    # a 9B file the owner added himself (Qwen 3.5, or one built on it such as Ornith): any Qwen 3.5 4B will do
    return next((n for n in names if QWEN35.search(n) and re.search(r"(?<![\d.])4b", n, re.I)), None)


_vectors: dict[str, list[float]] = {}  # text -> its meaning as numbers, kept while Jarvis runs


def embedder() -> str | None:
    """The installed model that turns text into meaning (Settings > Мозък > „Памет по смисъл“), if any."""
    return next((n for n in models() if "embed" in n.lower()), None)


def similar(query: str, texts: list[str], top: int = 8, post=None) -> list[int]:
    """The indexes of the ``texts`` closest in meaning to ``query``, best first ([] without an embedding model, or
    when Ollama does not answer): "колата ми" finds "карам синя Тойота", which shares no word with it."""
    model = embedder()
    if not model or not texts or not query.strip():
        return []
    missing = [t for t in dict.fromkeys([query, *texts]) if t not in _vectors]
    try:
        for start in range(0, len(missing), 128):
            batch = missing[start:start + 128]
            out = (post or _post)("/api/embed", {"model": model, "input": batch, "keep_alive": KEEP_LOADED})
            _vectors.update(zip(batch, out.get("embeddings") or []))
    except Exception as exc:
        log.warning("embedding failed: %s", exc)
        return []
    if query not in _vectors:
        return []

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        size = (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5
        return dot / size if size else 0.0

    q = _vectors[query]
    scored = sorted(((cosine(q, _vectors[t]), i) for i, t in enumerate(texts) if t in _vectors), reverse=True)
    return [i for score, i in scored[:top] if score > 0.45]


def to_messages(system: str, messages: list) -> list[dict]:
    """Claude-format messages -> Ollama chat messages. The tool results of the latest KEEP_FULL rounds go in full,
    older ones cut short and without their pictures (answers.shorten), so a long task fits the model's context."""
    out = [{"role": "system", "content": system}]
    names: dict[str, str] = {}  # tool_use id -> tool name
    rounds = [i for i, m in enumerate(messages) if m["role"] == "user" and not isinstance(m["content"], str)
              and any(_get(b, "type") == "tool_result" for b in m["content"])]
    old = set(rounds[:-KEEP_FULL])
    for index, message in enumerate(messages):
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
                images += [base64.b64encode(data).decode() for _mime, data in _images_of([b])]
            elif kind == "tool_result":
                result, pictures = _text_of(_get(b, "content")), _images_of(_get(b, "content"))
                if index in old:
                    result, pictures = shorten(result, len(pictures)), []
                if pictures:  # a screenshot a tool took: the model sees it with the result
                    images += [base64.b64encode(data).decode() for _mime, data in pictures]
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
                                              "parameters": normalize_schema(t.get("input_schema"))}}
            for t in tools if "input_schema" in t]


def from_reply(data: dict, model: str) -> Answer:
    message = data.get("message") or {}
    blocks = Reply()
    blocks.model = model
    text = re.sub(r"<think>.*?</think>", "", message.get("content") or "", flags=re.S).strip()
    written = written_call(text) if not message.get("tool_calls") else None
    if written:  # the call was written out instead of made: it is made
        blocks.append(Block("tool_use", id=f"call_{uuid.uuid4().hex[:12]}", name=written[0], input=written[1]))
        text = ""
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


def choose(model: str, tools: list) -> str | None:
    """The installed model for one step: ``model`` is "local", "local:<name>" or a name. A plain answer (no tools)
    goes to the small model beside it (4B beside 9B, much faster)."""
    name = pick((model.partition(":")[2] if is_local(model) else model) or None)
    if name and not tools:
        name = quick_partner(name) or name
    return name


class LocalBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with a model running in Ollama."""

    def __init__(self, post=None, start=None):
        self._post = post or _post
        self._start = start

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str) -> Answer:
        name = choose(model, tools)
        if not name and self._wake():
            name = choose(model, tools)
        if not name:
            raise RuntimeError(NOT_READY)
        model = name
        if not isinstance(system, str):
            system = "\n\n".join(_get(b, "text", "") for b in system)
        body = {
            "model": model, "stream": False, "messages": to_messages(system + SMALL_RULES, messages),
            "options": {**OPTIONS, "num_predict": min(max_tokens, 4096), "num_ctx": NUM_CTX},
            "keep_alive": KEEP_LOADED,
            # Thinking makes a laptop model slower but much smarter: on the hard steps only (brain._ask_local).
            "think": effort in ("high", "xhigh", "max"),
        }
        declared = to_tools(tools)
        if declared:
            body["tools"] = declared
        for _ in range(4):
            try:
                return from_reply(self._post("/api/chat", body), model)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else str(exc)
                # Not every model can think or use tools: ask again without what it can't do.
                if "think" in detail and "think" in body:
                    body.pop("think")
                elif "image" in detail and any("images" in m for m in body["messages"]):
                    for m in body["messages"]:  # a model added from a file without its vision part: words only
                        if m.pop("images", None):
                            m["content"] += "\n(A picture was attached; this brain cannot see pictures.)"
                elif "tools" in detail and "tools" in body:
                    body.pop("tools")
                else:
                    raise RuntimeError(f"The local brain (Ollama, {model}) failed: {detail}") from exc
            except urllib.error.URLError as exc:  # Ollama stopped in the middle (closed, or the laptop slept)
                raise RuntimeError(NOT_READY) from exc
        raise RuntimeError(f"The local brain (Ollama, {model}) did not answer.")

    def _wake(self) -> bool:
        """Ollama installed but not running (after a restart, before its tray app starts): start it once."""
        from . import localmodels

        try:
            started = (self._start or localmodels.start_ollama)()
        except Exception:
            return False
        if started:
            _seen["at"] = 0.0  # ask it again for its models
        return started


def _post(path: str, body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url() + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
