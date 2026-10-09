"""SQLite storage: conversation history, long-term memory, tasks, reminders, calendar, contacts."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, conversation TEXT NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, topic TEXT NOT NULL, fact TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, notes TEXT, due TEXT, priority TEXT DEFAULT 'normal',
    done INTEGER DEFAULT 0, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, at TEXT NOT NULL, repeat TEXT,
    channels TEXT DEFAULT 'local', fired INTEGER DEFAULT 0, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, start TEXT NOT NULL, end TEXT,
    location TEXT, notes TEXT, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY, goal TEXT NOT NULL, steps TEXT NOT NULL, status TEXT DEFAULT 'active',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY, user_id INTEGER, agent TEXT NOT NULL, tool TEXT NOT NULL, summary TEXT NOT NULL,
    ok INTEGER NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY, model TEXT NOT NULL, input INTEGER DEFAULT 0, output INTEGER DEFAULT 0,
    cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY, tier INTEGER NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY, app TEXT NOT NULL, sender TEXT NOT NULL, text TEXT NOT NULL, key TEXT NOT NULL,
    reply TEXT, state TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, phone TEXT, email TEXT,
    notes TEXT, created TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # SQLite's LIKE/NOCASE only fold ASCII; casefold() also handles Cyrillic ("мама" == "Мама").
        self.db.create_function("casefold", 1, lambda v: v.casefold() if isinstance(v, str) else v, deterministic=True)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def insert(self, table: str, **values) -> int:
        values.setdefault("created", now_iso())
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        return self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())).lastrowid

    # Conversation history (final text of each turn only; see brain.py)
    def add_message(self, conversation: str, role: str, content: str) -> None:
        self.insert("messages", conversation=conversation, role=role, content=content)

    def history(self, conversation: str, limit: int = 40) -> list[dict]:
        rows = self.query(
            "SELECT role, content FROM messages WHERE conversation=? ORDER BY id DESC LIMIT ?",
            (conversation, limit),
        )
        rows.reverse()
        # The API needs the history to start with a user turn.
        while rows and rows[0]["role"] != "user":
            rows.pop(0)
        return rows

    def clear_history(self, conversation: str) -> None:
        self.execute("DELETE FROM messages WHERE conversation=?", (conversation,))
