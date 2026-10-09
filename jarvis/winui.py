"""Programs on the screen by their words, not by pixels (Windows UI Automation).

Reading a window lists its buttons, links, fields, tabs and text by name, the way a screen reader sees them,
and a click goes to the middle of the element itself. That works in any program and in any browser, including
the owner's own one (Avast, Chrome, Edge) with their logins, and it is far more reliable than guessing
coordinates on a screenshot: the free brains often get pixels wrong (a click at y=891 on a 768-pixel screen).
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass

KINDS = {"ButtonControl": "button", "SplitButtonControl": "button", "HyperlinkControl": "link", "EditControl": "field",
         "ComboBoxControl": "dropdown", "CheckBoxControl": "checkbox", "RadioButtonControl": "option",
         "MenuItemControl": "menu", "TabItemControl": "tab", "ListItemControl": "item", "TreeItemControl": "item",
         "DataItemControl": "item", "TextControl": "text"}
SKIP_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
OWN = re.compile(r"^\s*J\.?A\.?R\.?V\.?I\.?S", re.I)  # Jarvis's own app window
MAX_ITEMS, MAX_TEXTS = 120, 60

_last: dict = {"handle": None, "items": []}  # the window read last and its elements (numbers for click_text)


@dataclass
class Element:
    kind: str
    name: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom in screen pixels
    control: object = None  # the live element, valid only during the call that found it

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.rect
        return (left + right) // 2, (top + bottom) // 2


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"'„“”«»…:!?.,]", " ", text or "")).strip().lower()


def best(items: list[Element], target: str) -> Element | None:
    """The element whose name matches ``target`` best: exact, then starting with it, then containing it, then
    containing all its words; buttons, links and fields before plain text."""
    want = norm(target)
    if not want:
        return None
    words = want.split()
    tests = [lambda n: n == want, lambda n: n.startswith(want), lambda n: want in n,
             lambda n: all(w in n for w in words)]
    for test in tests:
        found = [e for e in items if test(norm(e.name))]
        if found:
            return sorted(found, key=lambda e: e.kind == "text")[0]
    return None


def controls(items: list[Element]) -> list[Element]:
    """What can be clicked, numbered from 1 in read_window and click_text."""
    return [e for e in items if e.kind != "text"]


def render(title: str, items: list[Element]) -> str:
    lines = [f'Window: "{title}". Click with click_text by the words or the number.']
    for i, e in enumerate(controls(items), 1):
        lines.append(f'{i}. {e.kind} "{e.name}"' if e.name else f"{i}. {e.kind} (no name)")
    texts = [e.name for e in items if e.kind == "text"]
    if texts:
        lines.append("Text on it: " + " | ".join(texts[:MAX_TEXTS]))
    if len(lines) == 1:
        lines.append("Nothing readable here (a picture, a game or a canvas): use look_at_screen and control_input.")
    return "\n".join(lines)


def on_windows() -> bool:
    return sys.platform.startswith("win")


def _require_windows() -> None:
    if not on_windows():
        raise RuntimeError("Reading windows by their words works on Windows; use look_at_screen and control_input.")


def _uia():
    _require_windows()
    try:
        import pyautogui  # noqa: F401  (first: it makes this process DPI aware, so both speak in real pixels)
        import uiautomation as auto
    except ImportError as exc:
        raise RuntimeError("Missing a part for reading windows: in PowerShell run "
                           "`pip install uiautomation pyautogui` (or update Jarvis).") from exc
    return auto


def _rect(control) -> tuple[int, int, int, int]:
    r = control.BoundingRectangle
    return r.left, r.top, r.right, r.bottom


def _usable(window) -> bool:
    """A program's window: not Jarvis's own app, the taskbar or the desktop."""
    name = (window.Name or "").strip()
    return bool(name) and not OWN.match(name) and window.ClassName not in SKIP_CLASSES and name != "Program Manager"


def _windows(auto) -> list:
    return [w for w in auto.GetRootControl().GetChildren() if _usable(w)]


def _minimized(window) -> bool:
    return _rect(window)[0] <= -30000


def find_window(auto, title: str | None = None, handle: int | None = None):
    """The window whose title contains ``title``; else the one read last (``handle``) if it is still open; else the
    one in front, unless that is Jarvis's own app, then the topmost other one that is not minimized."""
    windows = _windows(auto)
    if not title and handle:
        found = [w for w in windows if w.NativeWindowHandle == handle]
        if found:
            return found[0]
    if title:
        low = title.lower()
        found = [w for w in windows if low in (w.Name or "").lower()]
        if not found:
            names = ", ".join(f'"{w.Name}"' for w in windows[:12])
            raise LookupError(f'No open window has "{title}" in its title. Open windows: {names}')
        return found[0]
    front = auto.GetForegroundControl()
    if front is not None and _usable(front):
        return front
    shown = [w for w in windows if not _minimized(w)]
    if not shown:
        raise LookupError("No program window is open.")
    return shown[0]


def bring_to_front(auto, window) -> None:
    """Put the window in front (restoring it if minimized), so clicks land on it and not on what covers it."""
    for attempt in range(2):
        try:
            if attempt:
                import pyautogui

                pyautogui.press("alt")  # Windows lets a program bring another window forward right after a key press
            window.SetActive(waitTime=0.3)
            if auto.GetForegroundWindow() == window.NativeWindowHandle:
                return
            window.SwitchToThisWindow(waitTime=0.3)
            if auto.GetForegroundWindow() == window.NativeWindowHandle:
                return
        except Exception:
            pass


def elements(window, seconds: float = 6.0, max_nodes: int = 6000) -> list[Element]:
    """The visible, named parts of a window in reading order (the walk stops after ``seconds``)."""
    import uiautomation as auto

    left, top, right, bottom = _rect(window)
    items, texts, start = [], 0, time.time()
    for n, (control, _depth) in enumerate(auto.WalkControl(window, maxDepth=60)):
        if n >= max_nodes or time.time() - start > seconds or len(items) >= MAX_ITEMS + MAX_TEXTS:
            break
        kind = KINDS.get(control.ControlTypeName)
        if kind is None:
            continue
        name = (control.Name or "").strip()
        if kind == "text" and (not name or texts >= MAX_TEXTS):
            continue
        try:
            if control.IsOffscreen:
                continue
            l, t, r, b = _rect(control)
        except Exception:
            continue
        if r <= l or b <= t or r <= left or l >= right or b <= top or t >= bottom:
            continue
        texts += kind == "text"
        items.append(Element(kind, name[:100], (l, t, r, b), control))
    return items


def read(title: str | None = None) -> str:
    auto = _uia()
    with auto.UIAutomationInitializerInThread():
        window = find_window(auto, title, _last["handle"])
        bring_to_front(auto, window)
        items = elements(window)
        if len(controls(items)) < 3:  # a browser turns its page on for readers on first ask
            time.sleep(0.8)
            items = elements(window)
        name, handle = window.Name, window.NativeWindowHandle
    _last.update(handle=handle, items=[Element(e.kind, e.name, e.rect) for e in controls(items)])
    return render(name, items)


def click(target: str, title: str | None = None, text: str | None = None, enter: bool = False) -> str:
    import pyautogui

    auto = _uia()
    target = str(target).strip()
    with auto.UIAutomationInitializerInThread():
        window = find_window(auto, title, _last["handle"])
        bring_to_front(auto, window)
        if target.isdigit():
            number = int(target)
            if _last["handle"] != window.NativeWindowHandle or not 0 < number <= len(_last["items"]):
                raise LookupError(f"No element number {target} from the last read_window: read the window again.")
            element = _last["items"][number - 1]
        else:
            items = elements(window)
            element = best(items, target)
            if element is None:
                names = ", ".join(f'"{e.name}"' for e in items if e.name and e.kind != "text")[:600]
                raise LookupError(f'Nothing called "{target}" in "{window.Name}". It has: {names}')
        name = window.Name
        _last["handle"] = window.NativeWindowHandle
    pyautogui.click(*element.center)
    if text:
        time.sleep(0.2)
        if element.kind == "field":
            pyautogui.hotkey("ctrl", "a")  # what is typed replaces what the field held
        type_text(text)
    if enter:
        pyautogui.press("enter")
    said = f'Clicked {element.kind} "{element.name}" in "{name}"'
    return said + (f' and typed "{text}"' if text else "") + ". Read the window again to check."


def type_text(text: str) -> None:
    """Type at the cursor; pyautogui only types plain ASCII, so anything else (e.g. Cyrillic) is pasted."""
    import pyautogui

    try:
        text.encode("ascii")
        pyautogui.write(text, interval=0.02)
    except UnicodeEncodeError:
        import pyperclip

        pyperclip.copy(text)
        pyautogui.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
