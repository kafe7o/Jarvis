"""More free brains, from a video on free AI API keys: NVIDIA and OpenRouter.

NVIDIA (build.nvidia.com, a free key without a card) hosts strong open models (Kimi, DeepSeek, Qwen, GLM,
gpt-oss) at about 40 requests a minute; OpenRouter (openrouter.ai/keys) has ":free" models at about 20 a minute
and 50 a day. Both speak the OpenAI chat format, so groq.py's translation is reused. Each one joins only when its
key is set: it takes over when Gemini's free requests run out, or is the main brain (JARVIS_MODEL=nvidia or
openrouter). Unlike Groq they take every tool and most of the conversation. Their models change often, so the
list is read from the provider itself and the strongest ones that can use tools go first.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import groq
from .gemini import Answer, _get

log = logging.getLogger("jarvis.freeapi")


@dataclass(frozen=True)
class Provider:
    key: str  # what JARVIS_MODEL says: "nvidia" or "nvidia:<model>"
    name: str
    url: str
    env: str  # the API key's setting
    site: str  # where the free key comes from
    fallback: tuple[str, ...]  # models to try when the list cannot be read


PROVIDERS = {
    "nvidia": Provider("nvidia", "NVIDIA", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "build.nvidia.com",
                       ("moonshotai/kimi-k2-instruct", "deepseek-ai/deepseek-v3.1", "openai/gpt-oss-120b",
                        "meta/llama-3.3-70b-instruct")),
    "openrouter": Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                           "openrouter.ai/keys", ("deepseek/deepseek-chat-v3.1:free", "openai/gpt-oss-120b:free",
                                                  "meta-llama/llama-3.3-70b-instruct:free")),
}
# Families that answer well in Bulgarian and use tools reliably, best first.
PREFER = ("kimi-k2", "deepseek", "glm", "qwen3", "minimax-m", "gpt-oss-120b", "mistral-large", "nemotron-super",
          "llama-4-maverick", "llama-3.3-70b", "gpt-oss-20b", "mistral", "gemma-3")
# Models that are not chat brains (search, safety, pictures, speech) or think for a long time before answering.
SKIP = re.compile(r"embed|rerank|guard|safety|reward|retriev|parse|ocr|clip|vision|-vl|audio|speech|tts|asr|whisper|"
                  r"translat|coder|math|detector|pii|base$")
SLOW = re.compile(r"thinking|-r1|reason")
BUDGET = 100_000  # characters of prompt (system, tools and conversation) sent at most
TRIES = 3  # models tried for one answer before handing over to the next free brain
DAILY = re.compile(r"per.?day|daily", re.I)


class UsedUp(RuntimeError):
    """This provider's free models are busy or used up for now."""


def provider_of(model: str | None) -> Provider | None:
    return PROVIDERS.get((model or "").partition(":")[0])


def is_free_api(model: str | None) -> bool:
    return provider_of(model) is not None


def available(model: str) -> bool:
    p = provider_of(model)
    return bool(p and os.environ.get(p.env))


def _natural(text: str) -> list:
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", text.lower())]


def rank(ids: list[str]) -> list[str]:
    """Preferred families first (in PREFER's order), newest version first within each; slow thinkers last."""
    def place(model: str) -> tuple:
        low = model.lower()
        family = next((i for i, name in enumerate(PREFER) if name in low), len(PREFER))
        return bool(SLOW.search(low)), family

    return sorted(sorted(ids, key=_natural, reverse=True), key=place)


def choose(provider: Provider, listing: list[dict]) -> list[str]:
    """The provider's models that suit Jarvis, best first. OpenRouter says which ones are free and can use tools;
    NVIDIA lists everything it hosts, so only the known good families are kept."""
    if provider.key == "openrouter":
        def free(m: dict) -> bool:
            price = m.get("pricing") or {}
            return m["id"].endswith(":free") or (str(price.get("prompt")) in ("0", "0.0")
                                                 and str(price.get("completion")) in ("0", "0.0"))

        ids = [m["id"] for m in listing if m.get("id") and free(m) and "tools" in (m.get("supported_parameters") or [])
               and not SKIP.search(m["id"].lower())]
        return rank(ids)
    ids = [m["id"] for m in listing if m.get("id") and not SKIP.search(m["id"].lower())]
    return rank([i for i in ids if any(name in i.lower() for name in PREFER)])


class FreeBrain:
    """Answers Claude-format requests (see brain.Jarvis._request) with one provider's free models."""

    def __init__(self, provider: str | Provider, post=None, get=None, wait=time.sleep):
        self.provider = PROVIDERS[provider] if isinstance(provider, str) else provider
        self._post = post or self._send
        self._get = get or self._fetch
        self._wait = wait
        self.resting: dict[str, float] = {}  # model ("*" = all of them) -> when it may be asked again
        self._listed: tuple[float, list[str]] = (0.0, [])

    def listing(self) -> list[str]:
        """The provider's good models, read at most every six hours (ten minutes after a failed read)."""
        at, models = self._listed
        if time.time() < at:
            return models
        try:
            models = choose(self.provider, self._get())
            until = time.time() + 6 * 3600
        except Exception as exc:
            log.warning("could not read %s's models: %s", self.provider.name, exc)
            models, until = [], time.time() + 600
        models = models or list(self.provider.fallback)
        self._listed = (until, models)
        return models

    def models(self, model: str) -> list[str]:
        p, now = self.provider, time.time()
        if self.resting.get("*", 0) > now:
            return []
        chosen = model.partition(":")[2] or (os.environ.get(f"JARVIS_{p.key.upper()}_MODEL") or "").strip()
        return [m for m in dict.fromkeys([chosen, *self.listing()]) if m and self.resting.get(m, 0) <= now][:TRIES]

    def create(self, *, model: str, system, messages: list, tools: list, max_tokens: int, effort: str) -> Answer:
        p = self.provider
        if not os.environ.get(p.env):
            raise RuntimeError(f"{p.env} is not set. Get a free key at {p.site}.")
        if not isinstance(system, str):
            system = "\n\n".join(_get(b, "text", "") for b in system)
        declared = groq.to_tools(tools, only=None)
        room = BUDGET - len(system) - len(json.dumps(declared, ensure_ascii=False))
        chat = groq.trim(groq.to_messages(system, messages), max(room, 8000))
        last = "no model to ask"
        for current in self.models(model):
            body = {"model": current, "messages": chat, "max_tokens": min(max_tokens, 4096)}
            if declared:
                body.update(tools=declared, tool_choice="auto")
            for attempt in range(2):
                try:
                    data = self._post(body)
                except urllib.error.HTTPError as exc:
                    code, detail, retry = exc.code, groq._detail(exc), groq._retry_after(exc)
                except (urllib.error.URLError, OSError) as exc:  # no connection or no answer in time
                    code, detail, retry = 0, str(exc), 0.0
                else:
                    if data.get("choices"):
                        return groq.from_reply(data, current, prefix=p.key)
                    error = data.get("error") or {}  # OpenRouter can say a model failed in a normal answer
                    code = int(error["code"]) if str(error.get("code", "")).isdigit() else 502
                    detail, retry = json.dumps(error, ensure_ascii=False) or "empty answer", 5.0
                last = f"{code} on {current}: {detail[:300]}"
                if code == 401:
                    raise RuntimeError(f"The {p.name} key is wrong. Get a new one at {p.site}.")
                if code == 429 and DAILY.search(detail):
                    self.resting["*"] = time.time() + 86400 - time.time() % 86400  # free again at midnight UTC
                    raise UsedUp(f"{p.name}'s free requests for today are used up.")
                if code == 429 and attempt == 0:
                    self._wait(min(retry or 5.0, 10))  # a few requests a minute: wait a moment
                    continue
                if code in (403, 404, 410) or (code == 400 and "tool" in detail.lower()):
                    rest = 6 * 3600  # not on the free tier, gone, or cannot use tools
                elif code == 402:
                    rest = 86400  # not free for this account
                elif code in (0, 408, 504):
                    rest = 600  # too slow right now
                else:
                    rest = 60  # busy, too big or down for a moment
                log.warning("%s: %s; trying the next model", p.name, last)
                self.resting[current] = time.time() + rest
                break
        raise UsedUp(f"{p.name} could not answer just now ({last}).")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "User-Agent": "jarvis",
                   "Authorization": f"Bearer {os.environ.get(self.provider.env, '')}"}
        if self.provider.key == "openrouter":
            headers["X-Title"] = "Jarvis"  # how the app shows on the person's OpenRouter page
        return headers

    def _fetch(self) -> list[dict]:
        req = urllib.request.Request(self.provider.url + "/models", headers=self._headers())
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("data") or []

    def _send(self, body: dict, timeout: float = 90) -> dict:
        req = urllib.request.Request(self.provider.url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
