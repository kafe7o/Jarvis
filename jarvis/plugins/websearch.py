"""Web search when Gemini is the brain (Claude has its own search built in), and watching videos with Gemini."""

from __future__ import annotations

from .. import gemini
from ..tools import ToolRegistry, obj


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Search the web with Google for current facts: news, prices, opening hours, weather, people, "
        "products. Returns an answer with source links.",
        obj({"query": ("string", "What to search for, in any language")}),
    )
    def google_search(query: str):
        return gemini.search(query, ctx.settings.model)

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
