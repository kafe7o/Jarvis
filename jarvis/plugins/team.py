"""A team of specialist agents that Jarvis manages.

Jarvis stays the one you talk to. For bigger jobs it hands parts to specialists, who work at the
same time, each with its own focus and only the abilities its job needs (never more than your
account allows), and share Jarvis's memory. Anything that needs your "yes" still asks you.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

from ..tools import ToolRegistry, obj

# key: (name, what they do, permission groups they work with)
TEAM = {
    "researcher": ("Изследователят", "researches anything on the web in depth, compares options and prices, checks facts "
                   "and returns a sourced summary", {"web", "browser", "memory"}),
    "marketing": ("Маркетологът", "content and marketing: ideas, scripts and captions for TikTok/Instagram/YouTube, "
                  "ad copy, posting plans, competitor research; drafts everything and posts only with approval",
                  {"web", "browser", "memory", "system"}),
    "inbox": ("Секретарят", "e-mail and messages: reads and sorts mail, saves reply drafts (never sends on its own), "
              "summarises what needs the owner", {"google", "comms", "messaging", "memory", "web"}),
    "organizer": ("Организаторът", "calendar, tasks, reminders and scheduled routines; plans the owner's day and week",
                  {"tasks", "google", "memory"}),
    "developer": ("Програмистът", "code, scripts, files and automation on the computer; builds new skills for Jarvis",
                  {"system", "agent", "web", "browser"}),
}

PERSONA = """

You are {name} ({key}), a specialist on Jarvis's team. Jarvis, the manager, gave you one assignment; the \
owner does not see your work directly, Jarvis reads your report and answers them. Your job: {job}.
Do the assignment completely with your tools, then reply with a short report: what you did, what you \
found (with links or file paths), and anything only the owner can decide. Write the report in the \
owner's language."""


def register(registry: ToolRegistry, ctx) -> None:
    roster = "; ".join(f"{key} = {name}: {job}" for key, (name, job, _g) in TEAM.items())

    @registry.tool(
        "Hand work to your team of specialist agents. They work at the same time, each with its own focus "
        "and tools, share your memory, and report back to you; then you answer the owner. Use it for bigger "
        "or parallel jobs (research + drafting + planning), not for quick one-step requests. Team: " + roster,
        obj({"assignments": ("array", "List of {agent: one of " + ", ".join(TEAM) + ", task: complete, "
                                      "self-contained instructions}")}),
        summarize=lambda a: "Екипът: " + "; ".join(f"{TEAM.get(x.get('agent'), (x.get('agent'),))[0]}: {x.get('task')}"
                                                   for x in a.get("assignments") or []),
    )
    def delegate(assignments: list):
        jarvis = getattr(ctx, "jarvis", None)
        if jarvis is None:
            raise RuntimeError("The team is not ready yet.")
        jobs = []
        for item in assignments[:6]:
            key = str(item.get("agent", "")).strip().lower()
            if key not in TEAM:
                raise ValueError(f"Unknown team member '{key}'. Choose from: {', '.join(TEAM)}")
            jobs.append((key, str(item.get("task", "")).strip()))
        if not jobs:
            raise ValueError("Give at least one assignment.")

        def work(job):
            key, task = job
            name, what, groups = TEAM[key]
            try:
                return name, jarvis.run_agent(task, PERSONA.format(name=name, key=key, job=what), groups, name)
            except Exception as exc:
                return name, f"Не успя: {exc}"

        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            # each specialist runs in a copy of this request's context: same person, same approvals
            futures = [pool.submit(contextvars.copy_context().run, work, job) for job in jobs]
            reports = [f.result() for f in futures]
        return "\n\n".join(f"## {name}\n{report}" for name, report in reports)
