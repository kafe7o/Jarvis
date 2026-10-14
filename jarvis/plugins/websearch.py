"""Web search when Gemini is the brain (Claude has its own search built in), and watching videos with Gemini."""

from __future__ import annotations

import logging
import re
import urllib.parse
from html import unescape

from .. import gemini
from ..tools import ToolRegistry, obj

log = logging.getLogger("jarvis.websearch")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", html))).strip()


def duckduckgo(query: str, fetch=None, limit: int = 8) -> str:
    """The first results of a DuckDuckGo search (no key, no AI): title, address and the snippet of each."""
    if fetch is None:
        from .daily import fetch
    page = fetch("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query}), timeout=10)
    found = []
    for block in re.split(r'class="result__a"', page)[1:limit + 1]:
        link = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not link:
            continue
        url = unescape(link.group(1))
        url = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("uddg", [url])[0]  # DuckDuckGo's redirect
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        found.append(f"- {_plain(link.group(2))} ({url}): {_plain(snippet.group(1)) if snippet else ''}")
    if not found:
        raise RuntimeError("DuckDuckGo returned no results.")
    return "Results (DuckDuckGo; open_page the best one if the snippets are not enough):\n" + "\n".join(found)


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Search the web with Google for current facts: news, prices, opening hours, weather, people, "
        "products. Returns an answer with source links.",
        obj({"query": ("string", "What to search for, in any language")}),
    )
    def google_search(query: str):
        model = ctx.settings.model if ctx.settings.model.startswith("gemini") else gemini.DEFAULT_MODEL
        try:
            answer = gemini.search(query, model)
        except Exception as exc:  # no Gemini key, or its searches are used up: the results page, without AI
            log.warning("Google search through Gemini failed (%s: %s); DuckDuckGo instead", type(exc).__name__,
                        str(exc)[:200])
            return duckduckgo(query)
        ctx.store.insert("usage", model=model)  # one free request, for the "Разход" tab
        return answer

    @registry.tool(
        "Watch a video and answer about it: a video file on this computer or a YouTube link. Uses Google "
        "Gemini (free, needs GEMINI_API_KEY even when Claude is the brain). Can take a few minutes for big files.",
        obj({"source": ("string", "Path of the video file, or a YouTube link"),
             "question?": ("string", "What to find out (default: describe everything in it)")}),
    )
    def watch_video(source: str, question: str = ""):
        if not gemini.available():
            raise RuntimeError("Watching videos needs a free Gemini key: aistudio.google.com/apikey, "
                               "then Settings > Connections > Gemini.")
        if "://" not in source:
            from .system import allowed_path

            source = str(allowed_path(source, ctx.settings.allowed_roots))
        return gemini.watch(source, question)
