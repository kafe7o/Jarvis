"""Keeping Jarvis up to date from GitHub: `jarvis update`, the app's "Update now" button, and a
background check that installs a new version by itself when Jarvis is idle.

Your .env, the .venv and everything in ~/.jarvis (memory, chats, accounts) stay as they are.
"""

from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "https://github.com/kafe7o/Jarvis/archive/refs/heads/{branch}.zip"
BRANCHES = ["main", "claude/jarvis-full-o9l6do"]
KEEP = {".env", ".venv", ".git"}
VERSION_FILE = ".jarvis-version"  # the commit installed last


def project_root() -> Path:
    """The folder this copy of Jarvis runs from (it is installed with `pip install -e`)."""
    return Path(__file__).resolve().parent.parent


def download() -> zipfile.ZipFile:
    for branch in BRANCHES:
        try:
            with urllib.request.urlopen(REPO.format(branch=branch), timeout=60) as resp:
                archive = zipfile.ZipFile(io.BytesIO(resp.read()))
        except Exception:
            continue
        if any(name.endswith("/jarvis/__main__.py") for name in archive.namelist()):
            return archive
    raise SystemExit("Не успях да изтегля Jarvis от GitHub. Провери интернета.")


def version_of(archive: zipfile.ZipFile) -> str:
    """GitHub stores the commit id as the archive's comment."""
    return archive.comment.decode(errors="replace").strip()


def installed_version(root: Path) -> str:
    try:
        return (root / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def newer(root: Path) -> zipfile.ZipFile | None:
    """The newest Jarvis from GitHub if it differs from the installed one, else None."""
    archive = download()
    version = version_of(archive)
    return archive if version and version != installed_version(root) else None


def install(root: Path, archive: zipfile.ZipFile, say=print) -> int:
    """Copy ``archive`` over ``root``; returns how many files were written."""
    if not (root / "pyproject.toml").exists() or not (root / "jarvis").is_dir():
        raise SystemExit(f"Пусни `jarvis update` в папката на Jarvis (не намирам pyproject.toml в {root}).")
    prefix = archive.namelist()[0].split("/")[0] + "/"
    old_project = (root / "pyproject.toml").read_text(encoding="utf-8")
    shutil.rmtree(root / "jarvis", ignore_errors=True)  # drop files that no longer exist upstream
    count = 0
    for info in archive.infolist():
        rel = info.filename[len(prefix):]
        if not rel or info.is_dir() or rel.split("/")[0] in KEEP:
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(info))
        count += 1
    say(f"Обнових {count} файла.")
    if (root / "pyproject.toml").read_text(encoding="utf-8") != old_project:
        say("Има нови пакети, инсталирам ги…")
        from .procs import run_capture

        run_capture([sys.executable, "-m", "pip", "install", "-q", "-e", f"{root}[all]"], timeout=1800)
    if version_of(archive):
        (root / VERSION_FILE).write_text(version_of(archive), encoding="utf-8")
    return count


def update(root: Path) -> None:
    """`jarvis update`."""
    archive = download()
    if version_of(archive) and version_of(archive) == installed_version(root):
        print("Jarvis вече е с последната версия.")
        restart_running_app()
        return
    install(root, archive)
    restart_running_app()
    print("Готово.")


def restart_running_app() -> None:
    """If the Jarvis app is running in the background, restart it so the new version loads."""
    import json
    import os

    port = os.environ.get("JARVIS_WEB_PORT", "8770")
    token = os.environ.get("JARVIS_WEB_TOKEN", "")
    if not token:
        return
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/restart", data=json.dumps({}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5):
            print("Рестартирах Jarvis с новата версия.")
    except Exception:
        pass
