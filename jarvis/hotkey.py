"""Ctrl+Alt+J anywhere on Windows: Jarvis's window comes to the front (or opens) and starts listening.

Windows' own hotkey (RegisterHotKey), no extra package. JARVIS_HOTKEY=0 turns it off."""

from __future__ import annotations

import logging
import os
import sys
import threading

log = logging.getLogger("jarvis.hotkey")

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
VK_J = 0x4A
WM_HOTKEY = 0x0312
SW_RESTORE = 9
TITLE = "J.A.R.V.I.S."  # the app's page title, which is its window's title


def own_window() -> int:
    """The handle of Jarvis's app window (0 when it is not open)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _param):
        if user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value.startswith(TITLE):
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(each, 0)
    return found[0] if found else 0


def bring_up(open_window, url: str) -> bool:
    """Jarvis's window in front, restored if minimized; opened when there is none. True when it was already open."""
    import ctypes

    hwnd = own_window()
    if not hwnd:
        open_window(url)
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)  # allowed: the hotkey's owner may bring a window forward
    return True


def start(on_press) -> threading.Thread | None:
    """Listen for Ctrl+Alt+J in the background and call ``on_press`` each time (Windows only)."""
    if not sys.platform.startswith("win") or os.environ.get("JARVIS_HOTKEY", "1").strip() in ("0", "no", "off"):
        return None

    def run():
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_J):
            log.warning("Ctrl+Alt+J is taken by another program; no hotkey for Jarvis")
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                try:
                    on_press()
                except Exception:
                    log.exception("the hotkey failed")

    thread = threading.Thread(target=run, name="jarvis-hotkey", daemon=True)
    thread.start()
    return thread
