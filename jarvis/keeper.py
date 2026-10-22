"""Jarvis runs in the background under a keeper, so he does not stop for good.

`jarvis app` (or the desktop icon) starts the keeper once, without a window and apart from PowerShell, and
opens the app window. The keeper runs Jarvis and starts him again a few seconds after his process stops for
any reason: a crash, an error at start, a restart for an update or an upgrade. Closing PowerShell or the app
window does not stop him; `jarvis stop` does. Only one keeper runs at a time (a lock file), so there is never a
second Jarvis on the same port. What Jarvis prints, and why he stopped, goes to ~/.jarvis/jarvis.log.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RESTART = 3  # exit code: start again at once (an update, an upgrade, „Рестартирай“)
STOP = 0  # exit code: stay stopped (`jarvis stop`)
KEPT = "JARVIS_KEPT"  # set in the environment of the Jarvis the keeper runs
MAX_LOG = 2_000_000
NO_WINDOW, NEW_GROUP, DETACHED = 0x08000000, 0x00000200, 0x00000008  # Windows process creation flags


def kept() -> bool:
    """True in the Jarvis the keeper runs (he ends to restart; the keeper starts him again)."""
    return bool(os.environ.get(KEPT))


def lock(path: Path):
    """Take the keeper's lock; None when another keeper holds it. The OS lets go of it when the process ends."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def start(cwd: str | None = None) -> None:
    """Start the keeper in the background: no window, and not tied to this console (closing it does not stop it)."""
    python = Path(sys.executable)
    if sys.platform.startswith("win"):
        quiet = python.with_name("pythonw.exe")
        exe, flags = (quiet, DETACHED | NEW_GROUP) if quiet.exists() else (python, NO_WINDOW | NEW_GROUP)
        subprocess.Popen([str(exe), "-m", "jarvis", "keeper"], cwd=cwd or os.getcwd(), creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    else:
        subprocess.Popen([str(python), "-m", "jarvis", "keeper"], cwd=cwd or os.getcwd(), start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _note(log: Path, text: str) -> None:
    with open(log, "a", encoding="utf-8") as out:
        out.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} keeper: {text}\n")


def _rotate(log: Path) -> None:
    try:
        if log.exists() and log.stat().st_size > MAX_LOG:
            os.replace(log, log.with_name(log.name + ".1"))
    except OSError:
        pass


def run(home: Path, popen=subprocess.Popen, sleep=time.sleep, rounds: int | None = None) -> None:
    """`jarvis keeper`: run Jarvis (`jarvis app --no-window`) and start him again whenever he stops, until he ends
    with STOP. A Jarvis that keeps failing right after starting is started again more slowly."""
    held = lock(home / "keeper.lock")
    if held is None:
        return  # a keeper is already running
    log = home / "jarvis.log"
    python = Path(sys.executable)
    console = python.with_name("python.exe")  # pythonw has no console streams for what Jarvis prints
    exe = console if sys.platform.startswith("win") and console.exists() else python
    flags = NO_WINDOW if sys.platform.startswith("win") else 0
    failures: list[float] = []
    while rounds is None or rounds > 0:
        rounds = None if rounds is None else rounds - 1
        _rotate(log)
        _note(log, "Jarvis starts")
        began = time.time()
        with open(log, "a", encoding="utf-8") as out:
            child = popen([str(exe), "-m", "jarvis", "app", "--no-window"],
                          env={**os.environ, KEPT: "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                          stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
            code = child.wait()
        if code == STOP:
            _note(log, "Jarvis was stopped (jarvis stop)")
            break
        if code == RESTART:
            _note(log, "Jarvis restarts")
            continue
        _note(log, f"Jarvis stopped unexpectedly (exit code {code}); starting him again")
        failures = [t for t in failures if began - t < 600] + [began]
        sleep(60 if len(failures) >= 5 else 3)  # failing again and again: give it a minute between tries
    held.close()
