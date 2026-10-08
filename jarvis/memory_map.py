"""The memory as a map, for the app's „Памет“ screen (like the graph view of the Obsidian vault in the owner's video).

Everything Jarvis knows becomes a point: remembered facts grouped by topic, people from the address book, notes
in the vault, open tasks, coming events and active plans. Lines join what belongs together: a fact to its topic,
a note to its folder and to the notes it links ([[...]]), and anything that mentions a person to that person.
While Jarvis works, ``touched`` says which points a tool just read or wrote, so they light up on the map.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

LIMITS = {"fact": 160, "note": 160, "person": 80, "task": 40, "event": 30, "plan": 12}
HUBS = {
    "facts": "Факти", "people": "Хора", "notes": "Бележки", "tasks": "Задачи", "calendar": "Календар",
    "plans": "Планове", "history": "Разговори",
}
FOLDERS = {"raw": "Входящи", "wiki": "Знание", "output": "Доклади"}
# Which part of the memory each tool works with, and whether it reads or writes it.
TOOLS = {
    "remember": ("facts", "write"), "recall": ("facts", "read"), "forget": ("facts", "write"),
    "save_contact": ("people", "write"), "find_contact": ("people", "read"), "list_contacts": ("people", "read"),
    "delete_contact": ("people", "write"),
    "vault_note": ("notes", "write"), "vault_write": ("notes", "write"), "vault_read": ("notes", "read"),
    "vault_search": ("notes", "read"), "vault_list": ("notes", "read"), "vault_done": ("notes", "write"),
    "add_task": ("tasks", "write"), "list_tasks": ("tasks", "read"), "complete_task": ("tasks", "write"),
    "update_task": ("tasks", "write"), "delete_task": ("tasks", "write"),
    "add_event": ("calendar", "write"), "list_events": ("calendar", "read"), "delete_event": ("calendar", "write"),
    "make_plan": ("plans", "write"), "update_plan_step": ("plans", "write"), "show_plan": ("plans", "read"),
    "search_history": ("history", "read"),
}
WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def short(text: str, n: int = 60) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def build(store, vault: Path | None) -> dict:
    nodes: list[dict] = [{"id": "me", "kind": "me", "label": "Ти"}]
    links: list[list[str]] = []
    texts: dict[str, str] = {}  # what each point says, to find the people it mentions

    def add(node_id: str, kind: str, label: str, parent: str, detail: str = "", key: str = "") -> None:
        nodes.append({"id": node_id, "kind": kind, "label": short(label, 48), "detail": short(detail, 280),
                      "key": key or label})
        links.append([parent, node_id])
        texts[node_id] = f"{label} {detail}".casefold()

    for hub, label in HUBS.items():
        nodes.append({"id": hub, "kind": "hub", "label": label})
        links.append(["me", hub])

    topics: set[str] = set()
    for f in store.query("SELECT id, topic, fact FROM facts ORDER BY id DESC LIMIT ?", (LIMITS["fact"],)):
        topic = (f["topic"] or "друго").strip().casefold()
        if topic not in topics:
            topics.add(topic)
            nodes.append({"id": f"topic:{topic}", "kind": "topic", "label": short(f["topic"] or "друго", 30)})
            links.append(["facts", f"topic:{topic}"])
        add(f"fact:{f['id']}", "fact", f["fact"], f"topic:{topic}", f["fact"], key=f["fact"])

    people = store.query("SELECT id, name, notes FROM contacts ORDER BY name LIMIT ?", (LIMITS["person"],))
    for p in people:
        add(f"person:{p['id']}", "person", p["name"], "people", p["notes"] or "")

    for t in store.query("SELECT id, title, due, notes FROM tasks WHERE done=0 ORDER BY id DESC LIMIT ?", (LIMITS["task"],)):
        add(f"task:{t['id']}", "task", t["title"], "tasks", " · ".join(x for x in (t["due"], t["notes"]) if x))
    soon = (datetime.now() + timedelta(days=30)).isoformat()
    for e in store.query("SELECT id, title, start, location FROM events WHERE start >= ? AND start <= ? ORDER BY start LIMIT ?",
                         (datetime.now().replace(hour=0, minute=0).isoformat(), soon, LIMITS["event"])):
        add(f"event:{e['id']}", "event", e["title"], "calendar", " · ".join(x for x in (e["start"][:16], e["location"]) if x))
    for p in store.query("SELECT id, goal, steps FROM plans WHERE status='active' ORDER BY id DESC LIMIT ?", (LIMITS["plan"],)):
        steps = json.loads(p["steps"] or "[]")
        done = sum(1 for s in steps if s.get("status") == "done")
        add(f"plan:{p['id']}", "plan", p["goal"], "plans", f"{done} от {len(steps)} стъпки")

    if vault is not None and vault.is_dir():
        notes = _notes(vault)
        for folder in sorted({n["folder"] for n in notes}):
            nodes.append({"id": f"folder:{folder}", "kind": "folder", "label": FOLDERS.get(folder, folder)})
            links.append(["notes", f"folder:{folder}"])
        stems = {n["stem"].casefold(): n["path"] for n in notes}
        paths = {n["path"].casefold().removesuffix(".md"): n["path"] for n in notes}
        for n in notes:
            add(f"note:{n['path']}", "note", n["stem"], f"folder:{n['folder']}", n["first"], key=n["path"])
            texts[f"note:{n['path']}"] = n["text"].casefold()
        for n in notes:  # [[links]] between notes, as Obsidian draws them
            for target in WIKILINK.findall(n["text"]):
                name = target.strip().casefold().removesuffix(".md")
                found = paths.get(name) or paths.get("wiki/" + name) or stems.get(name.rpartition("/")[2])
                if found and found != n["path"]:
                    links.append([f"note:{n['path']}", f"note:{found}"])

    for p in people:  # whatever mentions a person is joined to them
        name = p["name"].casefold().strip()
        if len(name) < 3:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
        for node_id, text in texts.items():
            if node_id != f"person:{p['id']}" and not node_id.startswith("person:") and pattern.search(text):
                links.append([f"person:{p['id']}", node_id])

    known = {n["id"] for n in nodes}
    unique = {tuple(link) for link in links if link[0] in known and link[1] in known}
    counts = {}
    for n in nodes:
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return {"nodes": nodes, "links": [list(link) for link in sorted(unique)], "counts": counts}


def _notes(vault: Path) -> list[dict]:
    out = []
    for path in sorted(vault.rglob("*.md"))[:3000]:
        rel = path.relative_to(vault).as_posix()
        if rel.startswith((".", "raw/_done/")) or rel == "README.md" or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:20000]
        except OSError:
            continue
        first = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), "")
        out.append({"path": rel, "stem": path.stem, "folder": rel.split("/")[0] if "/" in rel else "raw",
                    "text": text, "first": first, "mtime": path.stat().st_mtime})
    out.sort(key=lambda n: -n["mtime"])
    return out[: LIMITS["note"]]


def touched(graph: dict, tool: str, args: dict, result: str, limit: int = 12) -> tuple[str | None, str, list[str]]:
    """(the part of memory, "read" or "write", the points the tool's words and result name) for one finished step."""
    if tool not in TOOLS:
        return None, "", []
    hub, verb = TOOLS[tool]
    said = (json.dumps(args or {}, ensure_ascii=False) + "\n" + (result or "")).casefold()
    ids = []
    if tool == "remember":
        found = re.search(r"id (\d+)", result or "")
        ids = [f"fact:{found[1]}"] if found else []
    kinds = {"facts": ("fact", "topic"), "people": ("person",), "notes": ("note",), "tasks": ("task",),
             "calendar": ("event",), "plans": ("plan",)}.get(hub, ())
    for node in graph["nodes"]:
        if len(ids) >= limit:
            break
        if node["kind"] in kinds and node["id"] not in ids:
            key = str(node.get("key") or node["label"]).casefold()
            if len(key) >= 3 and key[:80] in said:
                ids.append(node["id"])
    return hub, verb, ids
