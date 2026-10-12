"""Reaching the owner when they are away: one question with the options, through every channel that gets to
them, then the task waits for the answer.

From the "let your agent call you" pattern (agentnative.inc, Bland.ai): an agent that is blocked on a decision
only its owner can make phones them, reads the options, repeats the answer back and carries on. Jarvis does the
free version: a push notification (ntfy, urgent when asked to "ring"), Telegram and the app, and keeps the
question as a notice so the owner's next message, wherever it comes from, is read as the answer and the task it
came from continues. The protocol (see PERSONA): only for a decision that blocks the task and that the owner
has to make, everything in one question, never twice in a row, and the answer counts for that task only.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from .. import notices
from ..tools import ToolRegistry, obj

log = logging.getLogger("jarvis.reach")

AGAIN_MINUTES = 30  # a second question this soon after an unanswered one is not sent: everything goes in one


def text_of(question: str, options: list | None, task: str) -> str:
    lines = [f"❓ {question.strip()}"]
    for n, option in enumerate([str(o).strip() for o in options or [] if str(o).strip()], 1):
        lines.append(f"{n}. {option}")
    if task.strip():
        lines.append(f"(задача: {task.strip()[:200]})")
    return "\n".join(lines)


def pending(store) -> list[dict]:
    """Questions sent to the owner that no message of theirs has answered yet, newest last."""
    return [r for r in notices.unseen(store) if r.get("kind") == "question"]


def register(registry: ToolRegistry, ctx) -> None:
    def owner_only() -> None:
        from ..brain import TURN

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner is asked this way.")

    @registry.tool(
        "Ask the owner a question when they are not here (away, asleep, a scheduled job) and the task cannot go on "
        "without their decision: it reaches them on their phone (push notification, Telegram) and in the app, and "
        "their next message, from anywhere, comes back to you as the answer with this task named, so you continue "
        "it then. Only for a decision that blocks the task and that only they can make; never for what you can "
        "settle from the code, your memory or a sensible default. Put every open question into this one call with "
        "the options; after it, stop the task with one line saying what you wait for.",
        obj({"question": ("string", "The question, short and plain, in the owner's language"),
             "options?": ("array", "The choices, if there are any (2-4 short lines)"),
             "task?": ("string", "The task this decides, in a few words, so you know what to continue later"),
             "ring?": ("boolean", "True when they said to call or wake them: the notification is urgent and loud")}),
    )
    def ask_owner(question: str, options: list | None = None, task: str = "", ring: bool = False):
        owner_only()
        question = " ".join(str(question or "").split())
        if not question:
            return "No question given."
        recent = [r for r in pending(ctx.store)
                  if datetime.fromisoformat(r["created"]) > datetime.now() - timedelta(minutes=AGAIN_MINUTES)]
        if recent:
            return (f"Not sent: you already asked the owner {recent[-1]['created'][11:16]} and they have not answered "
                    f"yet (\"{recent[-1]['text'][:160]}\"). Everything goes in one question; wait for their answer "
                    "and stop this task here with one line.")
        text = text_of(question, options, task or "")
        notices.add(ctx.store, text, kind="question")
        channels = ["the app"]
        if ring and os.environ.get("NTFY_TOPIC"):
            try:
                from .messaging import push_notify

                push_notify(text, title="Jarvis те търси", priority=5)
                channels.append("an urgent push notification")
            except Exception as exc:
                log.warning("urgent push failed: %s", exc)
        if ctx.notifiers:
            ctx.notify(text)
            channels.append("the notification channels (Telegram/push, the ones that are set up)")
        return (f"Asked the owner through {', '.join(channels)}. Nothing more to do on this until they answer: stop "
                "this task now with one line saying what you wait for. Their answer will come with their next "
                "message, together with this question, so you can continue the task then.")
