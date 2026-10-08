"""Jarvis changing his own code when the owner asks („надстрой се“, „промени си екрана“), safely.

An upgrade is a set of exact edits to files of the program (jarvis/...). Before it counts:
- the files it changes are copied to ~/.jarvis/upgrades/<n>/before/,
- the new code must still compile, import and load every plugin (``check``, in a separate Python),
- if the check fails, the old files are put back at once and the error goes back to Jarvis to fix.
``undo`` puts back the copy of any upgrade (the last one by default). If Jarvis cannot start after an
upgrade, the app undoes it by itself (``recover``). Updates from GitHub replace the program, so the upgrades
that are on are applied again after each update (``reapply``); one that no longer fits is reported.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

EDITABLE = (".py", ".html", ".css", ".js", ".json", ".md", ".txt")
# The safety net itself: an upgrade must never be able to break the way back.
PROTECTED = {"jarvis/upgrades.py", "jarvis/updater.py", "jarvis/__main__.py"}
TRIAL_SECONDS = 600  # an upgrade that has not started cleanly within this time is not undone by itself any more
_lock = threading.Lock()

# The program's own check, run in a fresh Python inside the folder being checked.
CHECK = r"""
import pathlib, sys, tempfile
bad = []
for path in sorted(pathlib.Path("jarvis").rglob("*.py")):
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        bad.append(f"{path}:{exc.lineno}: {exc.msg}")
if bad:
    sys.exit("Синтактична грешка:\n" + "\n".join(bad))
import jarvis.app, jarvis.brain, jarvis.hub, jarvis.router
from jarvis.config import Settings
from jarvis.plugins import Context, load_all
from jarvis.store import Store
from jarvis.tools import ToolRegistry
tmp = pathlib.Path(tempfile.mkdtemp())
s = Settings(); s.home = tmp / "home"; s.vault = tmp / "vault"
registry = ToolRegistry()
load_all(registry, Context(settings=s, store=Store(s.db_path)))
page = pathlib.Path("jarvis/web/index.html").read_text(encoding="utf-8")
if "</html>" not in page or page.count("<script") != page.count("</script>"):
    sys.exit("jarvis/web/index.html е недовършен (липсва </html> или </script>).")
print(len(registry.tools))
"""


def source_root() -> Path:
    """The folder of this copy of Jarvis (the one with pyproject.toml and jarvis/)."""
    return Path(__file__).resolve().parent.parent


def store_dir(home: Path) -> Path:
    return Path(home) / "upgrades"


def digest(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_path(rel: str) -> str:
    """A path inside jarvis/ that may be changed, written with forward slashes."""
    rel = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel.startswith("jarvis/"):
        rel = "jarvis/" + rel
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or "__pycache__" in parts:
        raise ValueError(f"Не може този път: {rel}")
    if not rel.endswith(EDITABLE):
        raise ValueError(f"Мога да променям само код и текст ({', '.join(EDITABLE)}), не {rel}")
    if rel in PROTECTED:
        raise ValueError(f"{rel} пази връщането назад и обновяването; него не го променям.")
    return rel


def read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def planned(root: Path, changes: list[dict]) -> dict[str, tuple[str | None, str]]:
    """{path: (text now, text after)} for ``changes``, checked before anything is written."""
    if not changes:
        raise ValueError("Няма промени.")
    out: dict[str, tuple[str | None, str]] = {}
    for n, change in enumerate(changes, 1):
        rel = safe_path(change.get("path", ""))
        now = out[rel][1] if rel in out else read(root, rel)
        if "content" in change and change.get("content") is not None:
            new = str(change["content"])
        else:
            find, replace = change.get("find"), change.get("replace")
            if not find or replace is None:
                raise ValueError(f"Промяна {n}: дай content (целия файл) или find + replace.")
            if now is None:
                raise ValueError(f"Промяна {n}: файлът {rel} го няма; за нов файл дай content.")
            found = now.count(find)
            if found != 1:
                raise ValueError(f"Промяна {n}: в {rel} текстът за find се среща {found} пъти, а трябва точно 1. "
                                 "Прочети файла отново (read_own_code) и копирай точния текст с повече редове около него.")
            new = now.replace(find, replace)
        out[rel] = (out[rel][0] if rel in out else now, new)
    return out


def check(root: Path) -> str | None:
    """None when the program in ``root`` still works, else what is wrong."""
    from .procs import decode, run_capture

    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}  # the code in root, not another copy
    env["PYTHONIOENCODING"] = "utf-8"
    code, out, _ = run_capture([sys.executable, "-c", CHECK], cwd=str(root), env=env, timeout=180)
    if code is None:
        return "Проверката не свърши за 3 минути."
    if code:
        return decode(out).strip()[-3000:] or "Проверката не мина."
    return None


def records(home: Path) -> list[dict]:
    out = []
    for path in sorted(store_dir(home).glob("*/upgrade.json"), key=lambda p: int(p.parent.name)):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def save(home: Path, record: dict) -> None:
    folder = store_dir(home) / str(record["id"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "upgrade.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def write(root: Path, texts: dict[str, str | None]) -> None:
    for rel, text in texts.items():
        target = root / rel
        if text is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")


def keep_before(home: Path, upgrade_id: int, before: dict[str, str | None]) -> None:
    folder = store_dir(home) / str(upgrade_id) / "before"
    shutil.rmtree(folder, ignore_errors=True)
    for rel, text in before.items():
        if text is not None:
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text, encoding="utf-8", newline="")


def before_of(home: Path, record: dict) -> dict[str, str | None]:
    folder = store_dir(home) / str(record["id"]) / "before"
    return {rel: (folder / rel).read_text(encoding="utf-8") if info["before"] else None
            for rel, info in record["files"].items()}


def apply(home: Path, what: str, changes: list[dict], root: Path | None = None, checker=None) -> dict:
    """Make one upgrade. Returns the record; ``state`` is "on", or "failed" with ``error`` (nothing changed)."""
    root, checker = root or source_root(), checker or check
    with _lock:
        texts = planned(root, changes)
        upgrade_id = max([r["id"] for r in records(home)] + [0]) + 1
        record = {"id": upgrade_id, "what": what.strip(), "created": datetime.now().isoformat(timespec="seconds"),
                  "state": "on", "trial": time.time(), "changes": changes,
                  "files": {rel: {"before": digest(now), "after": digest(new)} for rel, (now, new) in texts.items()}}
        keep_before(home, upgrade_id, {rel: now for rel, (now, _) in texts.items()})
        write(root, {rel: new for rel, (_, new) in texts.items()})
        error = checker(root)
        if error:
            write(root, {rel: now for rel, (now, _) in texts.items()})
            record.update(state="failed", error=error, trial=None)
        save(home, record)
        return record


def undo(home: Path, upgrade_id: int | None = None, root: Path | None = None, checker=None) -> dict:
    """Put back the files from before an upgrade (the last one that is on, by default)."""
    root, checker = root or source_root(), checker or check
    with _lock:
        on = [r for r in records(home) if r["state"] in ("on", "redo")]
        record = next((r for r in on if r["id"] == upgrade_id), None) if upgrade_id else (on[-1] if on else None)
        if record is None:
            raise ValueError("Няма такава надстройка, която да е включена." if upgrade_id else "Няма надстройки за връщане.")
        later = [r["id"] for r in on if r["id"] > record["id"] and r["state"] == "on" and set(r["files"]) & set(record["files"])]
        if later:
            raise ValueError(f"Надстройка {', '.join(map(str, later))} променя същите файлове след нея; върни първо нея.")
        if record["state"] == "on":
            back, before = {}, before_of(home, record)
            for rel, info in record["files"].items():
                now = read(root, rel)
                if digest(now) == info["before"]:
                    continue  # already as before (an update brought the old file back)
                if digest(now) != info["after"]:
                    raise ValueError(f"{rel} е променен след надстройката и не мога да го върна сам.")
                back[rel] = before[rel]
            write(root, back)
            error = checker(root) if back else None
            if error:  # the old code does not work either (should not happen): keep the upgrade
                write(root, {rel: read_after(root, record, rel) for rel in back})
                raise RuntimeError(f"Старият код не минава проверката, оставям надстройката: {error}")
        record.update(state="off", trial=None, undone=datetime.now().isoformat(timespec="seconds"))
        save(home, record)
        return record


def read_after(root: Path, record: dict, rel: str) -> str | None:
    texts = planned(root, [c for c in record["changes"] if safe_path(c.get("path", "")) == rel])
    return texts[rel][1]


def started(home: Path) -> list[str]:
    """The app started cleanly: the upgrades it runs with are kept from now on. Returns news for the owner
    (an upgrade undone because Jarvis could not start, or one an update left out) that was not told yet."""
    news = []
    for record in records(home):
        changed = bool(record.get("trial"))
        record["trial"] = None
        if record["state"] in ("off", "redo") and record.get("error") and not record.get("told"):
            news.append(f"Надстройка {record['id']} („{record['what']}“): {record['error']}")
            record["told"] = changed = True
        if changed:
            save(home, record)
    return news


def recover(home: Path, root: Path | None = None) -> dict | None:
    """The app could not start: undo the upgrade made just before (if any). Returns it."""
    trial = [r for r in records(home) if r["state"] == "on" and r.get("trial") and time.time() - r["trial"] < TRIAL_SECONDS]
    if not trial:
        return None
    record = undo(home, trial[-1]["id"], root=root, checker=lambda _root: None)
    record["error"] = "Jarvis не тръгна с тази надстройка, затова я върнах."
    save(home, record)
    return record


def reapply(home: Path, root: Path | None = None, checker=None) -> list[str]:
    """After an update from GitHub: apply again the upgrades that are on. Returns what to tell the owner."""
    root, checker = root or source_root(), checker or check
    said, done = [], []
    with _lock:
        pristine: dict[str, str | None] = {}
        for record in records(home):
            if record["state"] != "on":
                continue
            try:
                for rel in record["files"]:
                    pristine.setdefault(rel, read(root, rel))
                texts = planned_again(root, record)
            except ValueError:
                record.update(state="redo", error="не пасва на новата версия; кажи ми да я направя наново.", told=False)
                save(home, record)
                said.append(f"Надстройка {record['id']} („{record['what']}“) не пасва на новата версия; кажи ми да я направя наново.")
                continue
            keep_before(home, record["id"], {rel: now for rel, (now, _) in texts.items()})
            record["files"] = {rel: {"before": digest(now), "after": digest(new)} for rel, (now, new) in texts.items()}
            write(root, {rel: new for rel, (_, new) in texts.items()})
            done.append(record)
        error = checker(root) if done else None
        if error:
            write(root, pristine)
            for record in done:
                record.update(state="redo", error="не тръгна с новата версия; кажи ми да я направя наново.", told=False)
            said.append("Надстройките не тръгнаха с новата версия, махнах ги: "
                        + ", ".join(f"{r['id']} („{r['what']}“)" for r in done) + ". Кажи ми, ако искаш да ги направя наново.")
        for record in done:
            save(home, record)
    return said


def planned_again(root: Path, record: dict) -> dict[str, tuple[str | None, str]]:
    """``planned`` for an upgrade made on an older version: edits already in the new code are skipped."""
    changes = []
    for change in record["changes"]:
        rel = safe_path(change.get("path", ""))
        now = read(root, rel)
        if change.get("content") is not None:
            if now is not None and digest(now) not in (record["files"][rel]["before"], digest(change["content"])):
                raise ValueError(f"{rel} се е променил")
        elif now is not None and now.count(change.get("find") or "\0") != 1 and change.get("replace") in now:
            continue  # this edit is in the new version already
        changes.append(change)
    if not changes:
        return {}
    texts = planned(root, changes)
    return {rel: pair for rel, pair in texts.items() if pair[0] != pair[1]}


def describe(record: dict) -> str:
    states = {"on": "включена", "off": "върната", "failed": "не мина проверката", "redo": "трябва да се направи наново"}
    return (f"{record['id']}. {record['what']} ({record['created'][:16].replace('T', ' ')}, {states.get(record['state'], record['state'])}): "
            + ", ".join(record["files"]))
