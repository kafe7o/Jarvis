"""open_target with a program's name on Windows: found among the installed ones, started through the shell when
it asks to run elevated (Task Manager)."""
from __future__ import annotations

import os
import subprocess
import sys

from jarvis.plugins import system


def test_a_program_is_found_by_its_name_among_the_installed_ones(tmp_path, monkeypatch):
    """The owner's laptop: open_target("claude") -> "cannot find the file specified", though find_files had just
    found Claude.exe under AppData\\Local\\Programs; "taskmgr" -> "requires elevation"."""
    monkeypatch.setattr(sys, "platform", "win32")
    for var, folder in [("APPDATA", "roaming"), ("PROGRAMDATA", "programdata"), ("LOCALAPPDATA", "local"),
                        ("PROGRAMFILES", "pf"), ("PROGRAMFILES(X86)", "pf86")]:
        monkeypatch.setenv(var, str(tmp_path / folder))
    menu = tmp_path / "roaming/Microsoft/Windows/Start Menu/Programs"
    menu.mkdir(parents=True)
    (tmp_path / "local/Programs/claude").mkdir(parents=True)
    (tmp_path / "local/Programs/claude/Claude.exe").write_bytes(b"MZ")
    (tmp_path / "local/Programs/claude/Uninstall Claude.exe").write_bytes(b"MZ")
    (tmp_path / "pf/Discord Inc").mkdir(parents=True)
    (tmp_path / "pf/Discord Inc/Discord.exe").write_bytes(b"MZ")
    (menu / "Discord Inc").mkdir()
    (menu / "Discord Inc/Discord.lnk").write_bytes(b"L")
    assert system.windows_program("claude").endswith("Programs\\claude\\Claude.exe") or \
        system.windows_program("claude").endswith("Programs/claude/Claude.exe")
    assert system.windows_program("Claude.exe") == system.windows_program("claude")
    assert system.windows_program("discord").endswith("Discord.lnk")  # the Start Menu shortcut comes first
    for asked in ["task manager", "Task Manager", "таск мениджъра", "диспечер на задачите", "taskmgr"]:
        assert system.windows_program(asked) == "taskmgr", asked
    assert system.windows_program("настройки") == "ms-settings:"
    assert system.windows_program("nosuchthing") is None and system.windows_program("C:\\x\\y.exe") is None


def test_a_program_that_asks_to_run_elevated_is_started_through_the_shell(monkeypatch):
    class Elevation(OSError):
        winerror = 740

    started, shelled = [], []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", lambda t: started.append(t) or (_ for _ in ()).throw(Elevation(22, "elevation")),
                        raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: shelled.append(args))
    system._open_with_os("taskmgr")
    assert started == ["taskmgr"] and shelled == [["cmd", "/c", "start", "", "taskmgr"]]
