"""The memory in the notes vault, the way people use Obsidian as an AI's memory: every talk with the owner goes in
that day's note (daily/2026-10-22.md), each thing Jarvis notices about him goes in a note per topic in wiki/,
linked to the day it was said and listed in wiki/_master-index.md, and before each request the notes about it
are read back. Plain Markdown: the owner opens the folder in Obsidian and sees the graph grow.
JARVIS_JOURNAL=0 stops the daily notes."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from .plugins import vault

log = logging.getLogger("jarvis.journal")

DAILY = "daily"
SAID = 700  # characters of each answer kept in the day's note
RECALL_NOTES = 3  # notes read back before a request


def on() -> bool:
    return os.environ.get("JARVIS_JOURNAL", "1").strip().lower() not in ("0", "no", "off", "не")


def day_note(root: Path, when: datetime) -> Path:
    path = vault.inside(root, f"{DAILY}/{when:%Y-%m-%d}.md")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ndate: {when:%Y-%m-%d}\ntags: [daily]\n---\n# {when:%Y-%m-%d}\n\n"
                        f"Разговорите с Jarvis този ден. Всичко: [[_master-index]]\n", encoding="utf-8")
    return path


def write_down(root: Path, text: str, answer: str, when: datetime | None = None) -> Path:
    """The owner's words and Jarvis's answer at the end of that day's note."""
    when = when or datetime.now()
    path = day_note(vault.ensure(root), when)
    short = answer.strip() if len(answer) <= SAID else answer.strip()[:SAID].rsplit(" ", 1)[0] + " …"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {when:%H:%M}\n**Ти:** {text.strip()}\n\n**Jarvis:** {short}\n")
    return path


def file_fact(root: Path, topic: str, fact: str, when: datetime | None = None) -> Path:
    """A fact in its topic's note (wiki/<topic>.md), linked to the day it was said; a new topic joins the index."""
    when = when or datetime.now()
    root = vault.ensure(root)
    name = vault.file_name(topic.strip().capitalize())
    path = vault.inside(root, f"wiki/{name}.md")
    if not path.exists():
        path.write_text(f"---\ntags: [памет]\n---\n# {name}\n\nКакво Jarvis знае за това. Всичко: [[_master-index]]\n",
                        encoding="utf-8")
        index = vault.inside(root, vault.INDEX)
        listed = index.read_text(encoding="utf-8") if index.exists() else ""
        if f"[[{name}]]" not in listed:
            with index.open("a", encoding="utf-8") as fh:
                fh.write(f"- [[{name}]] - какво знам за това\n")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {fact.strip()} ([[{when:%Y-%m-%d}]])\n")
    return path


def recall(root: Path, about: str, limit: int = RECALL_NOTES) -> str:
    """The notes about ``about``, as lines for the brain ("" when there are none)."""
    from . import recipes

    words = " ".join(sorted(recipes.words(about) - recipes.GENERIC))
    if not words or not root.exists():
        return ""
    try:
        hits = vault.search(root, words, limit=limit)
    except OSError:
        return ""
    return "\n".join(f"- {h['path']}: " + " | ".join(h["lines"]) for h in hits if h["lines"])
