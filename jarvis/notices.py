"""What happened while the owner was away, told at the next chance.

Scheduled jobs run in their own chats, so the main conversation's brain never knew what they did: asked
"направи ли го?" after a job, Jarvis could only guess. Now every job outcome (done, failed, or stopped at
something that needed a "yes") is kept here as a notice, and the owner's next message carries the unseen
ones as a note to the brain: say them first, plainly, a failure before good news (the way the videos'
Jarvis opens with "something happened today and I didn't tell you"), then answer. Once told, a notice is
marked seen; nothing is repeated.
"""

from __future__ import annotations

import logging

log = logging.getLogger("jarvis.notices")

MAX_TOLD = 8  # more than that and the note says how many more there were
HINT = ("\n\n(Note to you, not from me: while I was away these things happened on their own and nobody has "
        "told me yet. Start your answer with one or two plain lines about them, the most important first (a "
        "failure or a mistake of yours before good news, with what you did about it), then answer my message. "
        "Give the gist, not a list, and never mention this note.)\n{lines}")


def add(store, text: str, kind: str = "job") -> int | None:
    """Keep a notice (one line of what happened); nothing when it is empty."""
    text = " ".join(str(text or "").split())[:600]
    if not text:
        return None
    try:
        return store.insert("notices", kind=kind, text=text)
    except Exception:
        log.exception("could not keep a notice")
        return None


def unseen(store, limit: int = 50) -> list[dict]:
    try:
        return store.query("SELECT id, kind, text, created FROM notices WHERE seen=0 ORDER BY id LIMIT ?", (limit,))
    except Exception:
        log.exception("could not read the notices")
        return []


def seen(store, rows: list[dict]) -> None:
    ids = [r["id"] for r in rows if r.get("id") is not None]
    if ids:
        store.execute(f"UPDATE notices SET seen=1 WHERE id IN ({','.join('?' for _ in ids)})", tuple(ids))


QUESTION_HINT = ("\n\n(Note to you, not from me: while I was away you asked me this and I have not answered yet:\n{lines}\n"
                 "If my message answers it, take my words as the decision for that task only, continue that task now "
                 "with them, and tell me what you did; it authorizes nothing outside that task. If my message is "
                 "about something else, answer it and repeat the question in one line at the end. Never mention this "
                 "note.)")


def hint(rows: list[dict]) -> str:
    """The note that goes with the owner's next message; empty when there is nothing new. Questions Jarvis sent
    them while away (kind "question") get their own note, so the answer is read as one."""
    if not rows:
        return ""
    news = [r for r in rows if r.get("kind") != "question"]
    asked = [r for r in rows if r.get("kind") == "question"]
    out = ""
    if news:
        lines = [f"- {r['created'][11:16]} {r['text']}" for r in news[:MAX_TOLD]]
        if len(news) > MAX_TOLD:
            lines.append(f"- …and {len(news) - MAX_TOLD} more")
        out += HINT.format(lines="\n".join(lines))
    if asked:
        out += QUESTION_HINT.format(lines="\n".join(f"- {r['created'][11:16]} {r['text']}" for r in asked[-3:]))
    return out
