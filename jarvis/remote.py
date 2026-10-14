"""Tasks from a folder: a text file dropped in ~/.jarvis/remote is a request from the owner.

Claude (or anything else the owner lets write to that folder, such as a synced folder) can give Jarvis a task
while the owner is away and see how fast he started on it. Each ``*.txt`` is asked in a chat of its own
(„Задачи отдалеч: <name>“) (with the same confirmations as the app) and gets an answer file next to it:
``<name>.answer.txt`` with when the task was picked up, when the first step started, which brains answered and
what Jarvis said. A first line „модел: groq“ (any JARVIS_MODEL value) runs that task on that brain.
A task cut off by a restart (an update) is asked again once. JARVIS_REMOTE_FOLDER=0 switches it off.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

CHAT = "Задачи отдалеч"
OFF = ("0", "false", "no", "не")


def folder(home: Path) -> Path:
    return Path(home) / "remote"


def waiting(where: Path) -> list[Path]:
    """The task files not yet taken, oldest first."""
    if not where.is_dir():
        return []
    files = [f for f in where.glob("*.txt") if not f.name.endswith((".answer.txt", ".working.txt"))]
    return sorted(files, key=lambda f: f.stat().st_mtime)


def clock(t: float | None) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "-"


def resume(where: Path) -> list[Path]:
    """Tasks cut off by a restart (an update, a crash) are asked again once, as ``<name>.again.txt``; one cut off
    twice gets an answer file saying so instead of a third try."""
    back = []
    for working in sorted(where.glob("*.working.txt")) if where.is_dir() else []:
        stem = working.name[:-len(".working.txt")]
        if stem.endswith(".again"):
            working.with_name(stem + ".answer.txt").write_text(
                "Задачата прекъсна два пъти (рестарт на Jarvis); не я пускам трети път.", encoding="utf-8")
            working.unlink(missing_ok=True)
        else:
            back.append(working.replace(working.with_name(stem + ".again.txt")))
    return back


def run_one(hub, task: Path) -> Path:
    """Ask Jarvis the task in ``task`` as the owner and write ``<name>.answer.txt``."""
    working = task.with_name(task.stem + ".working.txt")
    task.replace(working)  # taken: the next look does not ask it again
    text = working.read_text(encoding="utf-8", errors="replace").strip()[:4000]
    model = None
    first_line, _, rest = text.partition("\n")
    if re.fullmatch(r"(модел|model)\s*:\s*\S+", first_line.strip(), re.I):  # "модел: groq" tries one brain
        model, text = first_line.split(":", 1)[1].strip(), rest.strip()
    owner = hub.accounts.owner()
    picked = time.time()
    first, answer, error = None, "", False
    if not text:
        answer, error = "Празен файл.", True
    elif owner is None:
        answer, error = "Няма собственик в Jarvis.", True
    else:
        # one chat per task: the answers of earlier tasks must not steer this one (a free brain once carried an
        # old task's phone call into a search for apps)
        title = f"{CHAT}: {task.stem.removesuffix('.again')}"
        chat = next((c for c in hub.accounts.chats(owner.id) if c["title"] == title), None)
        chat = chat or hub.accounts.create_chat(owner.id, title)
        hub.events.add("chats", to=owner.id)
        from .brain import CHOSEN

        chosen = CHOSEN.set(model)
        try:
            answer = hub.ask(text, owner, chat["id"])
        except Exception as exc:
            log.exception("remote task failed")
            answer, error = str(exc) or "Грешка.", True
        finally:
            CHOSEN.reset(chosen)
        hub.accounts.touch_chat(chat["id"])
        hub.events.add("chats", to=owner.id)
        steps = [e for e in hub.events.events
                 if e["kind"] == "progress" and e.get("chat") == chat["id"] and e["time"] >= picked
                 and not str(e.get("tool", "")).startswith("level:")]  # a real tool, not "thinking at level N"
        first = steps[0]["time"] if steps else None
    done = time.time()
    used = [r["model"] for r in hub.ctx.store.query("SELECT model FROM usage WHERE created >= ? ORDER BY id",
                                                    (datetime.fromtimestamp(picked).isoformat(timespec="seconds"),))]
    lines = [f"Задача: {text}", f"Модел: {model or 'от настройките'}; отговориха: {', '.join(dict.fromkeys(used)) or '-'}",
             f"Поета: {clock(picked)}",
             f"Първа стъпка: {clock(first)}" + (f" (след {first - picked:.1f} s)" if first else " (без стъпки)"),
             f"Готово: {clock(done)} (след {done - picked:.1f} s)", f"Грешка: {'да' if error else 'не'}", "", answer]
    out = task.with_name(task.stem + ".answer.txt")
    out.write_text("\n".join(lines), encoding="utf-8")
    working.unlink(missing_ok=True)
    return out


def watch(hub, home: Path, every: float = 1.0, rounds: int | None = None) -> threading.Thread:
    """Look in the folder every second and ask the tasks one after another. ``rounds``: stop after that many looks (tests)."""
    where = folder(home)

    def loop():
        done = 0
        while rounds is None or done < rounds:
            done += 1
            if os.environ.get("JARVIS_REMOTE_FOLDER", "1").strip().lower() not in OFF:
                for task in waiting(where):
                    try:
                        run_one(hub, task)
                    except Exception:
                        log.exception("remote task %s", task.name)
            time.sleep(every)

    where.mkdir(parents=True, exist_ok=True)
    resume(where)
    thread = threading.Thread(target=loop, name="jarvis-remote-folder", daemon=True)
    thread.start()
    return thread
