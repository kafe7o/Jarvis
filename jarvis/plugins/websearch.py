"""Web search when Gemini is the brain (Claude has its own search built in)."""

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
