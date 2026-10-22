"""`jarvis backup` and `jarvis restore`: Jarvis's memory moved to another computer in one file.

The backup is a .zip in Documents with everything Jarvis learned and keeps in ~/.jarvis (memories, contacts, lessons,
recipes, chats, tasks, his own skills, SOUL.md and USER.md) and the keys from .env. It holds the keys, so it stays on
the owner's own disk or USB stick and is never sent to anyone. Restoring keeps the folder it replaces next to it
(.jarvis-before-restore-...), and a key the new computer already has stays as it is.
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

# Big or rebuilt by themselves: logs, Ollama's installer, browser profiles, caches, downloaded voice models.
SKIP_DIRS = {"browser", "browser-profile", "playwright", "cache", "__pycache__", "models", "tts-cache", "voice-cache"}
SKIP_SUFFIXES = {".log", ".exe", ".tmp", ".lock", ".gguf"}
ENV_NAME = "project.env"


def wanted(path: Path, home: Path) -> bool:
    rel = path.relative_to(home)
    return (not any(part in SKIP_DIRS or part.startswith(".jarvis-before") for part in rel.parts[:-1])
            and path.suffix.lower() not in SKIP_SUFFIXES and not path.name.endswith(("-wal", "-shm", "-journal")))


def backup(home: Path, env: Path, target_dir: Path | None = None) -> Path:
    """Write the backup .zip and return where it is."""
    target_dir = target_dir or Path.home() / "Documents"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"Jarvis-backup-{time.strftime('%Y-%m-%d-%H%M')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf, tempfile.TemporaryDirectory() as tmp:
        for path in sorted(home.rglob("*")):
            if not path.is_file() or not wanted(path, home):
                continue
            name = path.relative_to(home).as_posix()
            if path.suffix == ".db":  # a database in use is copied whole and consistent, not half-written
                copy = Path(tmp) / path.name
                src, dst = sqlite3.connect(path), sqlite3.connect(copy)
                try:
                    src.backup(dst)
                finally:  # closed at once: Windows cannot delete the copy while it is open
                    src.close()
                    dst.close()
                zf.write(copy, name)
            else:
                zf.write(path, name)
        if env.is_file():
            zf.write(env, ENV_NAME)
    return target


def restore(archive: Path, home: Path, env: Path) -> str:
    """Put a backup in place of ~/.jarvis (the old folder is kept beside it) and add its keys to .env."""
    from .setup_wizard import read_env, write_env

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        if not any(n.endswith(".db") or n.endswith(".md") for n in names):
            raise ValueError(f"{archive} не е резервно копие на Jarvis.")
        kept = None
        if home.exists() and any(home.iterdir()):
            kept = home.with_name(f".jarvis-before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                home.rename(kept)
            except OSError as exc:  # Windows: a file in it is open
                raise RuntimeError("Jarvis работи и държи паметта си отворена. Първо: jarvis stop, после пак "
                                   "jarvis restore.") from exc
        home.mkdir(parents=True, exist_ok=True)
        added = 0
        for name in names:
            if name == ENV_NAME:
                old = dict(read_env(env)) if env.is_file() else {}
                with tempfile.TemporaryDirectory() as tmp:
                    zf.extract(name, tmp)
                    saved = read_env(Path(tmp) / name)
                new = {k: v for k, v in saved.items() if v and not old.get(k)}
                if new:
                    write_env(env, {**old, **new})
                added = len(new)
                continue
            dest = (home / name).resolve()
            if home.resolve() not in dest.parents:  # a name like ../../x stays out
                continue
            zf.extract(name, home)
    note = f" Старата папка е запазена като {kept.name}." if kept else ""
    return f"Паметта е върната в {home}. Добавени ключове в .env: {added}.{note} Пусни: jarvis app"


def newest_backup() -> Path | None:
    """The newest Jarvis-backup-*.zip in Documents, Downloads or on the desktop (where a USB copy usually lands)."""
    home = Path.home()
    found = [f for d in ("Documents", "Downloads", "Desktop", "OneDrive/Desktop") for f in (home / d).glob("Jarvis-backup-*.zip")]
    return max(found, key=lambda f: f.stat().st_mtime, default=None)
