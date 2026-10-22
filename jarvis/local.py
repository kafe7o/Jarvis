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
NUM_CTX = 32768


def num_ctx() -> int:
    """JARVIS_LOCAL_CTX, read each time so a change in Settings applies at once."""
    try:
        return int(os.environ.get("JARVIS_LOCAL_CTX") or NUM_CTX)
    except ValueError:
        return NUM_CTX
DESCRIBE = """You are the eyes of an assistant that cannot see pictures. Describe this picture for it in plain \
text: which program or page is open, the visible words on buttons, links, fields, menus and messages, and where \
each is (top/bottom/left/right; pixel coordinates when you can tell). Say first what matters for the task. Only \
what is really there; under 200 words."""
_described: dict[str, str] = {}  # a picture (by its start, end and size) -> what the eyes said about it
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
    if names != _seen["models"]:
        _abilities.clear()  # a model added again under the same name may be able to do other things
    _seen.update(at=time.time(), models=names)
    return names


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


_abilities: dict[str, list[str] | None] = {}  # model -> what Ollama says it can do ("tools", "vision", "thinking")
_loaded: dict = {"at": 0.0, "models": []}


def abilities(name: str) -> list[str] | None:
    """What an installed model can do, as Ollama reports it; None when it cannot be asked (then: assume it can)."""
    if name not in _abilities:
        try:
            _abilities[name] = list(_post("/api/show", {"model": name}, timeout=3).get("capabilities") or []) or None
        except Exception:
            return None
    return _abilities[name]


def can(name: str, ability: str) -> bool:
    found = abilities(name)
    return found is None or ability in found


def loaded() -> list[str]:
    """The models Ollama holds in memory right now (asked at most every 5 s)."""
    if time.time() - _loaded["at"] > 5:
        try:
            with urllib.request.urlopen(url() + "/api/ps", timeout=0.8) as resp:
                _loaded["models"] = [m["name"] for m in json.loads(resp.read()).get("models", [])]
        except Exception:
            _loaded["models"] = []
        _loaded["at"] = time.time()
    return _loaded["models"]


def choose(model: str, tools: list, images: bool = False) -> str | None:
    """The installed model for one step: ``model`` is "local", "local:<name>" or a name.
    - A step with a picture goes to a model that can see (the chosen one if it can, else any installed one that can).
    - A plain answer (no tools) goes to the small model beside it (4B beside 9B, much faster), unless only the big
      one is in memory: on a 6 GB card the two do not fit together, and swapping them costs more than it saves.
    - A model added from a file that cannot use tools gives way to one that can.
    A model fixed by the owner ("local:<name>", the brain slider off Авто) takes every step but the pictures it
    cannot see."""
    wanted = (model.partition(":")[2] if is_local(model) else model) or None
    name = pick(wanted)
    if not name:
        return None
    others = [n for n in models() if n != name and "embed" not in n.lower()]
    if images and not can(name, "vision"):
        seeing = [n for n in others if abilities(n) and "vision" in abilities(n)]
        if seeing:
            return min(seeing, key=lambda n: (tools and not can(n, "tools"), not re.search(r"(?<![\d.])9b", n, re.I)))
        return name
    if wanted:  # the owner fixed this model (the brain slider): every step on it, only pictures go elsewhere
        return name
    if not tools:
        partner = quick_partner(name)
        in_memory = loaded()
        if partner and not (name in in_memory and partner not in in_memory) and (not images or can(partner, "vision")):
            return partner
        return name
    if not can(name, "tools"):
        return next((n for n in sorted(others, key=lambda n: not re.search(r"(?<![\d.])9b", n, re.I))
                     if abilities(n) and "tools" in abilities(n)), name)
    return name


class LocalBrain:
    """Answers Jarvis's requests (Anthropic-style message blocks, see brain.Jarvis._request) with a model running in Ollama."""

    def __init__(self, post=None, start=None, lines=None):
        self._post = post or _post
        self._start = start
        self._lines = lines or _lines

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str,
               on_text=None) -> Answer:
        """One step. ``on_text`` hears the answer as it is written (the app shows it growing), "" when the step
        turns out to be an action rather than words for the owner."""
        # The prompt's first block (persona, memories) stays the same from step to step; what changes (the clock)
        # goes with the newest message, so Ollama reuses the work it did on everything before it.
        parts = [system] if isinstance(system, str) else [_get(b, "text", "") for b in system]
        chat = to_messages(parts[0] + SMALL_RULES, messages)
        tail = "\n\n".join(p for p in parts[1:] if p)
        if tail:
            chat[-1] = {**chat[-1], "content": (chat[-1].get("content") or "") + "\n\n" + tail}
        images = any("images" in m for m in chat)
        name = choose(model, tools, images)
        if not name and self._wake():
            name = choose(model, tools, images)
        if not name:
            raise RuntimeError(NOT_READY)
        shot = images and not next(m for m in reversed(chat) if m.get("images")).get("content")
        thinker = choose(model, tools, False) if shot and tools else name
        if thinker and thinker != name:
            # A screenshot (it comes with no words) and the model that thinks can't see: the one with eyes says what
            # it shows, and the thinking model keeps every step (a screenshot used to hand the next four steps of a
            # task to the weaker model, and swap the two in the graphics card each time). A photo the owner sends
            # with a question still goes to the model with eyes.
            chat = self._described(chat, name)
            name = thinker
        model = name
        # Thinking makes a laptop model slower but much smarter: on the hard steps only (Jarvis._request). Its
        # thoughts count towards the words it may write, so a thinking step gets room for both.
        think = effort in ("high", "xhigh", "max")
        body = {
            "model": model, "stream": False, "messages": chat,
            "options": {**OPTIONS, "num_predict": min(max_tokens, 16384 if think else 4096), "num_ctx": num_ctx()},
            "keep_alive": KEEP_LOADED, "think": think,
        }
        declared = to_tools(tools)
        if declared:
            body["tools"] = declared
        if on_text:
            body["stream"] = True
        for _ in range(4):
            try:
                answer = from_reply(self._streamed(body, on_text) if on_text else self._post("/api/chat", body), model)
                if answer.stop_reason == "max_tokens" and not answer.content and body.get("think"):
                    body["think"] = False  # it thought until the end and said nothing: once more, straight to it
                    continue
                return answer
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

    def _described(self, chat: list[dict], seer: str) -> list[dict]:
        """``chat`` with its pictures turned into words by ``seer``: the newest picture described for the task at
        hand, the older ones only marked (each picture is described once, then remembered)."""
        task = next((m["content"][:600] for m in reversed(chat) if m["role"] == "user" and m.get("content")
                     and not m.get("images")), "")
        newest = max((i for i, m in enumerate(chat) if m.get("images")), default=-1)
        out = []
        for i, m in enumerate(chat):
            pictures = m.get("images")
            if not pictures:
                out.append(m)
                continue
            m = {k: v for k, v in m.items() if k != "images"}
            if i == newest:
                said = [self._describe(p, task, m.get("content") or "", seer) for p in pictures]
                m["content"] = (m.get("content") or "") + "".join(f"\n[What the picture shows, seen by {seer}: {s}]"
                                                                 for s in said)
            else:
                m["content"] = (m.get("content") or "") + "\n[an earlier picture]"
            out.append(m)
        return out

    def _describe(self, picture: str, task: str, context: str, seer: str) -> str:
        key = f"{len(picture)}:{picture[:200]}:{picture[-200:]}"
        if key not in _described:
            body = {"model": seer, "stream": False, "think": False, "keep_alive": "2m",
                    "options": {**OPTIONS, "num_predict": 600, "num_ctx": 8192},
                    "messages": [{"role": "system", "content": DESCRIBE},
                                 {"role": "user", "content": f"The task: {task}\n{context[:1500]}", "images": [picture]}]}
            try:
                said = re.sub(r"<think>.*?</think>", "", (self._post("/api/chat", body).get("message") or {})
                              .get("content") or "", flags=re.S).strip()
            except (urllib.error.URLError, OSError, ValueError) as exc:
                log.warning("%s could not describe a picture: %s", seer, exc)
                said = ""
            if len(_described) > 50:
                _described.clear()
            _described[key] = said or "(the picture could not be read)"
        return _described[key]

    def _streamed(self, body: dict, on_text) -> dict:
        """Ollama's answer piece by piece, put together as one reply; the words so far go to ``on_text`` a few times
        a second (not a tool call written out as text, and not while the step is calling tools)."""
        content, thinking, calls, last, shown = [], [], [], {}, 0.0
        for part in self._lines("/api/chat", body):
            if part.get("error"):
                raise RuntimeError(f"The local brain (Ollama, {body['model']}) failed: {part['error']}")
            message = part.get("message") or {}
            content.append(message.get("content") or "")
            thinking.append(message.get("thinking") or "")
            calls += message.get("tool_calls") or []
            if part.get("done"):
                last = part
                break
            if not calls and time.time() - shown > STREAM_EVERY:
                text = re.sub(r"<think>.*?(</think>|$)", "", "".join(content), flags=re.S).strip()
                if text and text[0] not in "{[<`":
                    on_text(text)
                    shown = time.time()
        if calls or shown and written_call("".join(content)):
            on_text("")  # this step acts: the words were not the answer
        return {**last, "message": {"role": "assistant", "content": "".join(content), "thinking": "".join(thinking),
                                    "tool_calls": calls}}

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


STREAM_EVERY = 0.4  # seconds between two looks at the growing answer


def _lines(path: str, body: dict, timeout: float = 600):
    """Ollama's streamed answer, one JSON object a line."""
    req = urllib.request.Request(url() + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            if line.strip():
                yield json.loads(line)


def _post(path: str, body: dict, timeout: float = 600) -> dict:
    req = urllib.request.Request(url() + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())
