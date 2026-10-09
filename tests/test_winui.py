from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from conftest import Approver
from jarvis import winui


class Ctl:
    """A window or a part of it, shaped like a uiautomation Control."""

    def __init__(self, kind, name, rect, children=(), cls="", handle=0, offscreen=False):
        self.ControlTypeName, self.Name, self.ClassName = kind, name, cls
        self.NativeWindowHandle, self.IsOffscreen, self.children = handle, offscreen, list(children)
        self.BoundingRectangle = SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])

    def GetChildren(self):
        return self.children

    def SetActive(self, waitTime=0):
        uia.front = self.NativeWindowHandle

    def SwitchToThisWindow(self, waitTime=0):
        uia.front = self.NativeWindowHandle


def walk(control, maxDepth=60, depth=0):
    for child in control.children:
        yield child, depth + 1
        yield from walk(child, maxDepth, depth + 1)


page = Ctl("DocumentControl", "Sign in - OpenRouter", (0, 80, 1366, 728), [
    Ctl("TextControl", "Sign in to OpenRouter", (500, 150, 860, 180)),
    Ctl("GroupControl", "", (400, 200, 960, 600), [
        Ctl("ButtonControl", "Continue with Google", (480, 300, 880, 340)),
        Ctl("EditControl", "Email address", (480, 380, 880, 420)),
        Ctl("ButtonControl", "Continue", (480, 440, 880, 480)),
        Ctl("HyperlinkControl", "Terms of Service", (480, 900, 600, 920), offscreen=True),  # scrolled away
    ]),
])
avast = Ctl("WindowControl", "Sign in - OpenRouter - Avast Secure Browser", (0, 0, 1366, 728), [
    Ctl("TabItemControl", "Sign in - OpenRouter", (0, 0, 240, 32)),
    Ctl("EditControl", "Address and search bar", (120, 40, 1100, 72)),
    page,
], handle=2)
own = Ctl("WindowControl", "J.A.R.V.I.S.", (0, 0, 1366, 728), handle=1)
taskbar = Ctl("PaneControl", "Taskbar", (0, 728, 1366, 768), cls="Shell_TrayWnd", handle=3)
uia = SimpleNamespace(front=1, GetRootControl=lambda: Ctl("PaneControl", "Desktop", (0, 0, 1366, 768), [own, taskbar, avast]),
                      WalkControl=walk)


class Session:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def windows(monkeypatch):
    clicks, typed = [], []
    uia.front = 1  # Jarvis's own app is in front, as when the owner just asked
    uia.GetForegroundWindow = lambda: uia.front
    uia.GetForegroundControl = lambda: {1: own, 2: avast}[uia.front]
    uia.UIAutomationInitializerInThread = Session
    monkeypatch.setitem(sys.modules, "uiautomation", uia)
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(
        click=lambda x, y: clicks.append((x, y)), write=lambda text, interval=0: typed.append(text),
        press=lambda key: typed.append(f"<{key}>"), hotkey=lambda *keys: typed.append("+".join(keys))))
    monkeypatch.setattr(winui, "on_windows", lambda: True)
    monkeypatch.setattr(winui, "_last", {"handle": None, "items": []})
    return SimpleNamespace(clicks=clicks, typed=typed)


def test_he_reads_the_owners_browser_by_its_words(registry, windows):
    out, err = registry.run("read_window", {"window": "avast"}, Approver())
    assert not err and uia.front == 2  # brought to the front, over Jarvis's own window
    assert '2. field "Address and search bar"' in out and '3. button "Continue with Google"' in out
    assert '4. field "Email address"' in out and "Text on it: Sign in to OpenRouter" in out
    assert "Terms of Service" not in out and "Taskbar" not in out  # scrolled away; not a program window


def test_he_clicks_by_the_words_and_types_without_coordinates(registry, windows):
    registry.run("read_window", {"window": "Avast"}, Approver())
    uia.front = 1
    out, err = registry.run("click_text", {"target": "continue with google"}, Approver())
    assert not err and windows.clicks == [(680, 320)] and "Continue with Google" in out  # the middle of the button
    assert uia.front == 2  # the window read last came to the front first
    out, err = registry.run("click_text", {"target": "4", "text": "anastas@example.com", "enter": True}, Approver())
    assert not err and windows.clicks[-1] == (680, 400) and windows.typed == ["ctrl+a", "anastas@example.com", "<enter>"]
    out, err = registry.run("click_text", {"target": "Continue"}, Approver())
    assert windows.clicks[-1] == (680, 460)  # the exact name wins over "Continue with Google"
    out, err = registry.run("click_text", {"target": "Вход с Apple"}, Approver())
    assert err and "Continue with Google" in out  # says what there is instead of clicking at random


def test_without_windows_or_the_window_he_says_so(registry, windows, monkeypatch):
    out, err = registry.run("read_window", {"window": "Chrome"}, Approver())
    assert err and "Avast Secure Browser" in out and "J.A.R.V.I.S" not in out
    monkeypatch.setattr(winui, "on_windows", lambda: False)
    out, err = registry.run("read_window", {}, Approver())
    assert err and "look_at_screen" in out


def test_a_click_outside_the_screenshot_is_refused(registry, monkeypatch):
    from PIL import Image as Picture

    clicks = []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(
        screenshot=lambda: Picture.new("RGB", (1366, 768)), click=lambda x, y: clicks.append((x, y))))
    registry.run("look_at_screen", {}, Approver())
    out, err = registry.run("control_input", {"action": "click", "x": 893, "y": 891}, Approver())
    assert err and "outside the screenshot (1280x719)" in out and not clicks
    out, err = registry.run("control_input", {"action": "click", "x": 640, "y": 360}, Approver())
    assert not err and clicks == [(683, 384)]  # scaled to the real screen
