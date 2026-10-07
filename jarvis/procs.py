"""Run a program and collect its output without hanging.

`subprocess.run(capture_output=True)` waits until every process holding the output pipe closes it.
On Windows a background service a command starts (the adb server after `adb devices`, for one)
inherits that pipe and keeps it open forever, so Jarvis stood on "running a command" with no end.
Here output goes to temporary files instead, Jarvis waits only for the program itself, and on
timeout the whole process tree is stopped.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile

WINDOWS = sys.platform.startswith("win")


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def run_capture(args, *, shell: bool = False, cwd: str | None = None, timeout: float = 120,
                merge: bool = True) -> tuple[int | None, bytes, bytes]:
    """Run ``args``; return (exit code or None on timeout, stdout, stderr).

    With ``merge`` stderr is folded into stdout (and the returned stderr is empty)."""
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {"start_new_session": True}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(args, shell=shell, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT if merge else err, **extra)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            code = None
        out.seek(0)
        err.seek(0)
        return code, out.read(), err.read()


def run_text(args, *, shell: bool = False, cwd: str | None = None, timeout: float = 120) -> str:
    """Run and describe the result the way the tools report it to Claude."""
    code, out, _ = run_capture(args, shell=shell, cwd=cwd, timeout=timeout)
    text = decode(out)
    if code is None:
        return f"stopped after {timeout:g} seconds (the command did not finish)\n{text}"
    return f"exit code {code}\n{text}"


def decode(data: bytes) -> str:
    if WINDOWS:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            import locale
            return data.decode(locale.getpreferredencoding(False) or "cp1251", errors="replace")
    return data.decode("utf-8", errors="replace")
