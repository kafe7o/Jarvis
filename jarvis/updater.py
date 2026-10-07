"""`jarvis update`: download the newest Jarvis from GitHub and install it over this copy.

Your .env, the .venv and everything in ~/.jarvis (memory, chats, accounts) stay as they are.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "https://github.com/kafe7o/Jarvis/archive/refs/heads/{branch}.zip"
BRANCHES = ["main", "claude/jarvis-full-o9l6do"]
KEEP = {".env", ".venv", ".git"}


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


def update(root: Path) -> None:
    if not (root / "pyproject.toml").exists() or not (root / "jarvis").is_dir():
        raise SystemExit(f"Пусни `jarvis update` в папката на Jarvis (не намирам pyproject.toml в {root}).")
    archive = download()
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
    print(f"Обнових {count} файла.")
    if (root / "pyproject.toml").read_text(encoding="utf-8") != old_project:
        print("Има нови пакети, инсталирам ги…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", f"{root}[all]"], check=False)
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
