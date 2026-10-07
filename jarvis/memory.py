"""Long-term memory for Jarvis.

Facts about the user are stored in a local SQLite database and survive
between sessions. Before each request, the most relevant facts are picked
for the user's message and added to the system prompt sent to Claude.
Claude itself decides what to remember or forget through three tools
(`remember_fact`, `forget_fact`, `recall_facts`).

Typical use from the chat loop::

    from jarvis.memory import MemoryStore, MEMORY_TOOLS

    memory = MemoryStore()                      # ~/.jarvis/memory.db
    system = BASE_PROMPT + memory.prompt_section(user_message)
    response = client.messages.create(
        model=..., system=system, tools=[*other_tools, *MEMORY_TOOLS], ...
    )
    # for each tool_use block whose name is in MEMORY_TOOL_NAMES:
    result = memory.handle_tool(block.name, block.input)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".jarvis" / "memory.db"


def default_path() -> Path:
    """JARVIS_MEMORY_PATH if set, else ~/.jarvis/memory.db."""
    return Path(os.environ.get("JARVIS_MEMORY_PATH", DEFAULT_PATH))

# Facts in this category are about who the user is (name, language, home
# city...) and are always included in the prompt, whatever the message.
PROFILE = "profile"
CATEGORIES = (PROFILE, "preference", "person", "project", "general")

_WORD = re.compile(r"\w+", re.UNICODE)
# Short function words that would otherwise match almost every fact.
_STOPWORDS = {
    # Bulgarian
    "и", "в", "във", "на", "за", "да", "се", "е", "са", "съм", "си", "ли", "от", "с", "със",
    "по", "че", "не", "но", "а", "как", "какво", "кой", "коя", "кое", "кои", "мен", "ме",
    "ми", "моя", "моят", "моите", "мой", "мое", "ти", "те", "той", "тя", "то", "ние", "вие",
    "това", "тази", "този", "тези", "има", "беше", "ще", "до", "при", "към", "или", "аз",
    # English
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "be",
    "my", "me", "i", "you", "your", "it", "this", "that", "what", "who", "how", "do", "does",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 1 and w not in _STOPWORDS}


def _same_word(a: str, b: str) -> bool:
    # Crude stemming so inflected forms match ("куче"/"кучето",
    # "сестра"/"сестрата", "meeting"/"meetings"): words count as the same when
    # they share their first four letters. Short words must match exactly.
    # Good enough for a few hundred personal facts, with no language-specific
    # dependency.
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]


@dataclass
class Fact:
    id: int
    text: str
    category: str
    created_at: str
    updated_at: str
    use_count: int = 0

    def as_line(self) -> str:
        return f"[{self.id}] ({self.category}) {self.text}"


class MemoryStore:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path is not None else default_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'general',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                use_count   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- storage -----------------------------------------------------------

    def add(self, text: str, category: str = "general") -> Fact:
        """Store a fact. Re-adding the same fact (ignoring case) only touches it."""
        text = " ".join(text.split())
        if not text:
            raise ValueError("fact text is empty")
        if category not in CATEGORIES:
            category = "general"
        existing = self._db.execute(
            "SELECT * FROM facts WHERE lower(text) = lower(?)", (text,)
        ).fetchone()
        if existing:
            self._db.execute(
                "UPDATE facts SET category = ?, updated_at = ? WHERE id = ?",
                (category, _now(), existing["id"]),
            )
            self._db.commit()
            return self.get(existing["id"])
        now = _now()
        cur = self._db.execute(
            "INSERT INTO facts (text, category, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (text, category, now, now),
        )
        self._db.commit()
        return self.get(cur.lastrowid)

    def update(self, fact_id: int, text: str) -> Fact | None:
        text = " ".join(text.split())
        cur = self._db.execute(
            "UPDATE facts SET text = ?, updated_at = ? WHERE id = ?", (text, _now(), fact_id)
        )
        self._db.commit()
        return self.get(fact_id) if cur.rowcount else None

    def forget(self, fact_id: int) -> bool:
        cur = self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        self._db.commit()
        return cur.rowcount > 0

    def get(self, fact_id: int) -> Fact | None:
        row = self._db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return Fact(**dict(row)) if row else None

    def all(self) -> list[Fact]:
        rows = self._db.execute("SELECT * FROM facts ORDER BY id").fetchall()
        return [Fact(**dict(r)) for r in rows]

    # --- retrieval ---------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[Fact]:
        """Facts sharing words with `query`, best match first."""
        q = _tokens(query)
        if not q:
            return []
        scored = []
        for fact in self.all():
            words = _tokens(fact.text)
            overlap = sum(1 for qw in q if any(_same_word(qw, w) for w in words))
            if overlap:
                # Prefer facts that match more of the query, then recently
                # updated ones, then ones that were useful before.
                scored.append((overlap, fact.updated_at, fact.use_count, fact))
        scored.sort(key=lambda s: s[:3], reverse=True)
        return [s[3] for s in scored[:limit]]

    def relevant(self, message: str, limit: int = 12) -> list[Fact]:
        """Profile facts plus the facts that best match `message`."""
        profile = [f for f in self.all() if f.category == PROFILE]
        seen = {f.id for f in profile}
        matches = [f for f in self.search(message, limit) if f.id not in seen]
        facts = profile + matches[: max(0, limit - len(profile))]
        if matches:
            ids = [f.id for f in matches]
            self._db.execute(
                f"UPDATE facts SET use_count = use_count + 1 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            self._db.commit()
        return facts

    def prompt_section(self, message: str, limit: int = 12) -> str:
        """Text to append to the system prompt for this user message."""
        facts = self.relevant(message, limit)
        lines = [
            "",
            "# Long-term memory",
            "You remember things about the user between conversations. Use the",
            "remember_fact tool when the user tells you something worth keeping",
            "(who they are, preferences, people, plans), forget_fact when a fact is",
            "wrong or the user asks you to forget it, and recall_facts to look up",
            "something not listed below. Do not store passwords or card numbers.",
        ]
        if facts:
            lines.append("")
            lines.append("What you know that may be relevant now:")
            lines.extend(f"- {f.as_line()}" for f in facts)
        else:
            lines.append("")
            lines.append("You don't know anything relevant about the user yet.")
        return "\n".join(lines) + "\n"

    # --- Claude tools ------------------------------------------------------

    def handle_tool(self, name: str, tool_input: dict) -> str:
        """Run one of MEMORY_TOOLS and return the tool_result text."""
        try:
            if name == "remember_fact":
                if tool_input.get("replaces_id") is not None:
                    fact = self.update(int(tool_input["replaces_id"]), tool_input["text"])
                    if fact is None:
                        return f"No fact with id {tool_input['replaces_id']}."
                else:
                    fact = self.add(tool_input["text"], tool_input.get("category", "general"))
                return f"Remembered: {fact.as_line()}"
            if name == "forget_fact":
                fid = int(tool_input["id"])
                return f"Forgot fact {fid}." if self.forget(fid) else f"No fact with id {fid}."
            if name == "recall_facts":
                query = tool_input.get("query", "")
                facts = self.search(query, int(tool_input.get("limit", 10))) if query else self.all()
                return "\n".join(f.as_line() for f in facts) or "Nothing found."
        except (KeyError, ValueError) as e:
            return f"Error: {e}"
        return f"Unknown memory tool: {name}"


MEMORY_TOOLS = [
    {
        "name": "remember_fact",
        "description": (
            "Save a lasting fact about the user so you know it in future conversations: "
            "name, family and friends, preferences, habits, work, goals, important dates. "
            "Write it as one short self-contained sentence. To correct an existing fact, "
            "pass its id as replaces_id. Never store passwords, card numbers or other secrets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The fact, e.g. 'Anastas drinks coffee without sugar.'"},
                "category": {
                    "type": "string",
                    "enum": list(CATEGORIES),
                    "description": "'profile' for core identity facts that should always be in context.",
                },
                "replaces_id": {"type": "integer", "description": "Id of a fact this one corrects."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "forget_fact",
        "description": "Delete a remembered fact by id, when it is wrong or the user asks you to forget it.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "recall_facts",
        "description": "Search long-term memory for facts about the user. Empty query lists everything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
]
MEMORY_TOOL_NAMES = {t["name"] for t in MEMORY_TOOLS}


def _main(argv: list[str] | None = None) -> None:
    """Small CLI to inspect and edit memory by hand: list | add TEXT | forget ID | search QUERY | export."""
    import argparse

    p = argparse.ArgumentParser(prog="python -m jarvis.memory", description=_main.__doc__)
    p.add_argument("--db", help="database path (default: $JARVIS_MEMORY_PATH or ~/.jarvis/memory.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("text", nargs="+")
    a.add_argument("--category", default="general", choices=CATEGORIES)
    f = sub.add_parser("forget")
    f.add_argument("id", type=int)
    s = sub.add_parser("search")
    s.add_argument("query", nargs="+")
    sub.add_parser("export")
    args = p.parse_args(argv)

    store = MemoryStore(args.db)
    if args.cmd == "list":
        for fact in store.all():
            print(fact.as_line())
    elif args.cmd == "add":
        print(store.add(" ".join(args.text), args.category).as_line())
    elif args.cmd == "forget":
        print("forgotten" if store.forget(args.id) else "not found")
    elif args.cmd == "search":
        for fact in store.search(" ".join(args.query)):
            print(fact.as_line())
    elif args.cmd == "export":
        print(json.dumps([vars(f) for f in store.all()], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
