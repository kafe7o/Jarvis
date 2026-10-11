"""The optimizer (from the owner's video "Jarvis audited every one of your agents while you were asleep"):
a look over everything Jarvis believes and keeps, to find what contradicts itself or went stale, fix what is
safe to fix, and leave the rest for the owner's yes.

``audit`` checks the remembered facts (duplicates, several facts on one topic), the lessons (duplicates),
the plans (stale ones), the contacts (one number under two names), the skills (ones that no longer parse),
the recent failures, and the routines. Safe fixes (exact duplicates) are applied at once; conflicts are listed
for Jarvis to resolve in the audit routine, and anything about people, money or permissions is only reported.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import core_files

STALE_PLAN_DAYS = 14


def norm(text: str) -> str:
    return " ".join(str(text or "").casefold().split()).strip(" .!")


def audit(store, home: Path, days: int = 7, fix: bool = True) -> dict:
    """Everything found, as {"fixed": [...], "conflicts": [...], "stale": [...], "broken": [...], "failures": str}.
    With ``fix`` the safe repairs (exact duplicates) are made."""
    fixed: list[str] = []
    conflicts: list[str] = []
    stale: list[str] = []
    broken: list[str] = []

    # Facts: exact duplicates go; a topic with several different facts may hold a contradiction.
    facts = store.query("SELECT id, topic, fact, created FROM facts ORDER BY id")
    seen: dict[tuple[str, str], dict] = {}
    for f in facts:
        key = (norm(f["topic"]), norm(f["fact"]))
        if key in seen:
            if fix:
                store.execute("DELETE FROM facts WHERE id=?", (f["id"],))
            fixed.append(f"duplicate fact removed: [{f['topic']}] {f['fact'][:80]}")
        else:
            seen[key] = f
    by_topic: dict[str, list[dict]] = {}
    for f in seen.values():
        by_topic.setdefault(norm(f["topic"]), []).append(f)
    for topic, rows in by_topic.items():
        if len(rows) > 1 and topic not in ("", "general", "общо"):
            conflicts.append(f"facts on '{rows[0]['topic']}' may contradict each other: "
                             + " | ".join(f"#{r['id']} ({r['created'][:10]}) {r['fact'][:100]}" for r in rows[:5]))

    # Lessons: exact duplicates go (core_files.learn already refuses them, but the owner edits the file too).
    lessons = [line for line in core_files.read(home, "LESSONS.md").splitlines() if line.strip()]
    kept, known = [], set()
    for line in lessons:
        key = norm(line.lstrip("- "))
        if line.startswith("- ") and key in known:
            fixed.append(f"duplicate lesson removed: {line[2:80]}")
            continue
        known.add(key)
        kept.append(line)
    if fix and len(kept) != len(lessons):
        core_files.write(home, "LESSONS.md", "\n".join(kept))
    opposites = [("никога", "винаги"), ("не питам", "питам"), ("without asking", "ask")]
    texts = [line[2:] for line in kept if line.startswith("- ")]
    for a, b in opposites:
        with_a = [t for t in texts if a in t.casefold()]
        with_b = [t for t in texts if b in t.casefold() and a not in t.casefold()]
        for ta in with_a[:3]:
            for tb in with_b[:3]:
                shared = set(re.findall(r"\w{5,}", ta.casefold())) & set(re.findall(r"\w{5,}", tb.casefold()))
                if len(shared) >= 2:
                    conflicts.append(f"lessons may contradict each other: '{ta[:90]}' vs '{tb[:90]}'")

    # Plans: active ones nobody touched for a while.
    cutoff = (datetime.now() - timedelta(days=STALE_PLAN_DAYS)).isoformat()
    for p in store.query("SELECT id, goal, steps, created FROM plans WHERE status='active' AND created < ?", (cutoff,)):
        steps = json.loads(p["steps"])
        done = sum(st["status"] in ("done", "skipped", "failed") for st in steps)
        stale.append(f"plan {p['id']} '{p['goal'][:80]}' from {p['created'][:10]}: {done} of {len(steps)} steps finished")

    # Contacts: the same number under two names.
    numbers: dict[str, list[str]] = {}
    for c in store.query("SELECT name, phone FROM contacts WHERE phone IS NOT NULL AND phone != ''"):
        numbers.setdefault(re.sub(r"\D", "", c["phone"]), []).append(c["name"])
    for number, names in numbers.items():
        if len(names) > 1:
            conflicts.append(f"contacts {', '.join(names)} share the number {number}")

    # Skills: ones that no longer parse (a schema problem is fixed automatically when the tool is registered).
    for path in sorted((Path(home) / "skills").glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError) as exc:
            broken.append(f"skill {path.stem}: {type(exc).__name__}: {exc}")

    # Failures lately.
    since = (datetime.now() - timedelta(days=max(1, days))).isoformat()
    rows = store.query("SELECT tool, ok FROM activity WHERE created >= ?", (since,))
    counts: dict[str, list[int]] = {}
    for r in rows:
        counts.setdefault(r["tool"], [0, 0])[0 if r["ok"] else 1] += 1
    worst = sorted(((bad, good, tool) for tool, (good, bad) in counts.items() if bad), reverse=True)[:8]
    failures = (f"{len(rows)} steps in the last {days} days, {sum(b for b, _g, _t in worst)} failed"
                + (": " + ", ".join(f"{t} {b}/{b + g}" for b, g, t in worst) if worst else "."))
    return {"fixed": fixed, "conflicts": conflicts, "stale": stale, "broken": broken, "failures": failures,
            "facts": len(seen), "lessons": len([l for l in kept if l.startswith('- ')])}


def report(found: dict, when: datetime | None = None) -> str:
    """The audit as a short Markdown report (also saved to the vault's output/ by the routine)."""
    when = when or datetime.now()
    lines = [f"# Одит на Jarvis, {when:%Y-%m-%d %H:%M}", "",
             f"Факти: {found['facts']}. Уроци: {found['lessons']}. {found['failures']}", ""]
    lines += ["## Поправено само (безопасно)"] + ([f"- {x}" for x in found["fixed"]] or ["- нищо"]) + [""]
    lines += ["## Противоречия (чакат решение)"] + ([f"- {x}" for x in found["conflicts"]] or ["- няма"]) + [""]
    lines += ["## Застояло"] + ([f"- {x}" for x in found["stale"]] or ["- няма"]) + [""]
    lines += ["## Счупено"] + ([f"- {x}" for x in found["broken"]] or ["- няма"])
    return "\n".join(lines)


def work_summary(store, days: int = 1) -> str:
    """What Jarvis did in the last ``days``, with numbers: steps by kind, tasks done, reminders fired, messages
    answered, jobs run, failures. For the morning brief ("over the last days I found 312 businesses...")."""
    since = (datetime.now() - timedelta(days=max(1, days))).replace(microsecond=0).isoformat()
    rows = store.query("SELECT tool, ok, agent FROM activity WHERE created >= ?", (since,))
    by_tool: dict[str, int] = {}
    for r in rows:
        by_tool[r["tool"]] = by_tool.get(r["tool"], 0) + 1
    top = sorted(by_tool.items(), key=lambda kv: -kv[1])[:8]
    agents = {r["agent"] for r in rows if r["agent"] and r["agent"] != "Jarvis"}
    requests = store.query("SELECT COUNT(*) AS n FROM messages WHERE role='user' AND created >= ?", (since,))[0]["n"]
    tasks_done = store.query("SELECT COUNT(*) AS n FROM tasks WHERE done=1 AND created >= ?", (since,))[0]["n"]
    fired = store.query("SELECT COUNT(*) AS n FROM reminders WHERE fired=1 AND at >= ?", (since,))[0]["n"]
    answered = store.query("SELECT COUNT(*) AS n FROM inbox WHERE reply IS NOT NULL AND created >= ?", (since,))[0]["n"]
    failed = sum(1 for r in rows if not r["ok"])
    lines = [f"Last {days} day(s): {requests} requests, {len(rows)} steps ({failed} failed), {tasks_done} tasks done, "
             f"{fired} reminders/jobs fired, {answered} messages answered for the owner."]
    if top:
        lines.append("Most used: " + ", ".join(f"{t} ×{n}" for t, n in top))
    if agents:
        lines.append("Specialists that worked: " + ", ".join(sorted(agents)))
    return "\n".join(lines)
