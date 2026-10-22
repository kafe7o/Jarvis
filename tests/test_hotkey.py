"""Ctrl+Alt+J: only on Windows, and off with JARVIS_HOTKEY=0."""

from jarvis import hotkey


def test_no_hotkey_off_windows_or_when_turned_off(monkeypatch):
    assert hotkey.start(lambda: None) is None or hotkey.sys.platform.startswith("win")
    monkeypatch.setattr(hotkey.sys, "platform", "win32")
    monkeypatch.setenv("JARVIS_HOTKEY", "0")
    assert hotkey.start(lambda: None) is None
