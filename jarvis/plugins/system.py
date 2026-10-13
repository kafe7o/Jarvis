"""Control of the computer: shell, programs, files, clipboard, keyboard/mouse, screenshots."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from html import unescape
from pathlib import Path

from .. import browsers, winui
from ..procs import run_capture, run_text
from ..tools import Image, ToolRegistry, obj

log = logging.getLogger("jarvis.system")

MAX_OUTPUT = 20_000


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n... ({len(text) - MAX_OUTPUT} more characters)"


def _open_with_os(target: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def page_text(html: str) -> tuple[str, str]:
    """(title, readable text) of a web page: scripts, styles and tags out, whitespace squeezed."""
    html = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1\s*>", " ", html)
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    html = re.sub(r"(?is)<head\b.*?</head\s*>", " ", html)
    text = re.sub(r"\s+", " ", unescape(re.sub(r"(?s)<[^>]+>", " ", html))).strip()
    return (re.sub(r"\s+", " ", unescape(title.group(1))).strip() if title else ""), text


def around(text: str, words: str, size: int = 4000) -> str:
    """The parts of ``text`` around ``words`` (like ctrl+f), else its start; at most ``size`` characters."""
    found = []
    for word in [w for w in re.split(r"[,;|]", words or "") if w.strip()][:6]:
        for m in list(re.finditer(re.escape(word.strip()), text, re.I))[:3]:
            found.append((max(0, m.start() - 250), m.end() + 350))
    if not found:
        return text[:size] + (" …" if len(text) > size else "")
    parts, end = [], -1
    for a, b in sorted(found):  # overlapping parts become one
        if parts and a <= end:
            parts[-1] = (parts[-1][0], max(b, end))
        else:
            parts.append((a, b))
        end = max(end, b)
    return " … ".join(text[a:b] for a, b in parts)[:size]


def allowed_path(path: str, allowed: list[str]) -> Path:
    """``path`` if it is inside one of the allowed folders (JARVIS_FILE_ROOTS)."""
    p = Path(path).expanduser().resolve()
    roots = [Path(r).expanduser().resolve() for r in allowed]
    if roots and not any(p == r or r in p.parents for r in roots):
        raise PermissionError(f"{p} is outside the allowed folders ({', '.join(map(str, roots))}). Set JARVIS_FILE_ROOTS.")
    return p


def register(registry: ToolRegistry, ctx) -> None:
    def facts() -> list[str]:
        """What is remembered about the owner, for the browser they want (see browsers.wanted_by_facts)."""
        store = getattr(ctx, "store", None)
        try:
            return [row["fact"] for row in store.query("SELECT fact FROM facts")] if store is not None else []
        except Exception:
            return []

    def safe_path(path: str) -> Path:  # reads the setting each time, so a change in Settings applies at once
        # Files attached in the app land in ~/.jarvis/uploads and are always his to read.
        return allowed_path(path, [*ctx.settings.allowed_roots, str(Path(ctx.settings.home) / "uploads")])

    @registry.tool(
        "Run a shell command on this computer and return its output. Use for anything the other tools "
        "don't cover (installing software, git, system settings, scripts).",
        obj({
            "command": ("string", "The command line"),
            "cwd?": ("string", "Working directory"),
            "timeout?": ("integer", "Seconds before giving up (default 120)"),
        }),
        confirm=True,
        local=True,
        summarize=lambda a: f"Изпълни команда: {a.get('command')}",
    )
    def run_shell(command: str, cwd: str | None = None, timeout: int = 120):
        refused = browsers.by_hand(command)
        if refused:
            raise RuntimeError(refused)
        return _clip(run_text(command, shell=True, cwd=cwd and str(Path(cwd).expanduser()), timeout=timeout))

    @registry.tool(
        "Open a web address in the owner's own browser (the one chosen in Settings, else their default one; e.g. "
        "Avast) as a new tab, or a file, folder or program with the system (e.g. '~/Documents/report.pdf', "
        "'spotify', 'calc'). The only way to open a page for the owner: never start a browser with run_shell, "
        "run_python or pyautogui and never search the disk for it. Then work on the page with read_window and "
        "click_text.",
        obj({"target": ("string", "URL, path or program name")}),
    )
    def open_target(target: str):
        target = (target or "").strip()
        bare_domain = (re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", target, re.I) and not Path(target).expanduser().exists()
                       and not re.search(r"\.(pdf|docx?|xlsx?|pptx?|txt|md|png|jpe?g|gif|mp[34]|zip|exe|py|csv|json|html?)$", target, re.I))
        if "://" in target or target.startswith("www.") or bare_domain:
            name = browsers.open_url(target, facts())
            word = name.split()[0] if name != "the default browser" else "браузъра"
            return (f"Opened {target} in {name}, the owner's browser. It is a tab of that window: read_window(\"{word}\") "
                    "shows the page in a moment (a new tab takes a second or two to load), then click_text by its words.")
        path = Path(target).expanduser()
        if path.exists():
            _open_with_os(str(path))
            return f"Opened {path}."
        exe = shutil.which(target)
        if exe:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return f"Started {target}."
        _open_with_os(target)
        return f"Asked the system to open {target}."

    @registry.tool(
        "The fast way to use a web page: opens the address in the owner's own browser (they see it) and returns "
        "the page's text in the same step, so there is no need to read the window or look at the screen after it. "
        "With find, the text around those words comes back (like ctrl+f), e.g. find='USD' on an exchange-rate page. "
        "Answer from that text; read_window and click_text are for clicking on the page, look_at_screen only for "
        "what has no words.",
        obj({"url": ("string", "The web address, e.g. 'bnb.bg' or a search URL"),
             "find?": ("string", "Words to find on the page, separated by commas")}),
    )
    def open_page(url: str, find: str = ""):
        from .daily import fetch

        url = (url or "").strip()
        url = url if "://" in url else "https://" + url
        name = browsers.open_url(url, facts())
        word = name.split()[0] if name != "the default browser" else "браузъра"
        opened = f"Opened {url} in {name} (a tab of that window: read_window(\"{word}\") / click_text work on it)."
        try:
            title, text = page_text(fetch(url, timeout=8))
        except Exception as exc:
            title, text = "", ""
            log.info("could not read %s directly: %s", url, exc)
        if len(text) >= 300:  # a page with its words in it; a script-only page (or a sign-in) is read on the screen
            return f"{opened} The page \"{title}\" says:\n{around(text, find)}"
        if not winui.on_windows():
            return opened + " Its text could not be read directly; read_window shows it."
        time.sleep(2)  # the new tab loads
        try:
            return f"{opened} The browser window shows now:\n{winui.read(word)}"
        except Exception as exc:
            return opened + f" (Could not read it yet: {exc}; read_window in a moment.)"

    @registry.tool(
        "List a folder's contents (optionally matching a glob pattern, recursive with '**/').",
        obj({"path": ("string", "Folder"), "pattern?": ("string", "Glob, e.g. '*.pdf' or '**/*.py'")}),
    )
    def list_dir(path: str, pattern: str = "*"):
        base = safe_path(path)
        items = []
        for p in sorted(base.glob(pattern))[:500]:
            stat = p.stat()
            items.append({
                "name": str(p.relative_to(base)),
                "type": "dir" if p.is_dir() else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
            })
        return items

    @registry.tool("Read a text file.", obj({"path": ("string", "File path")}))
    def read_file(path: str):
        return _clip(safe_path(path).read_text(encoding="utf-8", errors="replace"))

    @registry.tool(
        "Write (or append to) a text file, creating folders as needed.",
        obj({"path": ("string", "File path"), "content": ("string", "Text"), "append?": ("boolean", "Append instead of overwrite")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"{'Допиши във' if a.get('append') else 'Запиши'} файл {a.get('path')} ({len(a.get('content', ''))} знака)",
    )
    def write_file(path: str, content: str, append: bool = False):
        p = safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if append else "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"Wrote {p}."

    @registry.tool(
        "Move or rename a file or folder.",
        obj({"source": ("string", "From"), "destination": ("string", "To")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Премести {a.get('source')} → {a.get('destination')}",
    )
    def move_path(source: str, destination: str):
        return f"Moved to {shutil.move(str(safe_path(source)), str(safe_path(destination)))}."

    @registry.tool(
        "Copy a file or folder.",
        obj({"source": ("string", "From"), "destination": ("string", "To")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Копирай {a.get('source')} → {a.get('destination')}",
    )
    def copy_path(source: str, destination: str):
        src, dst = safe_path(source), safe_path(destination)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return f"Copied to {dst}."

    @registry.tool(
        "Delete a file or folder (folders recursively).",
        obj({"path": ("string", "What to delete")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"ИЗТРИЙ {a.get('path')}",
    )
    def delete_path(path: str):
        p = safe_path(path)
        if p in roots:
            raise PermissionError("Refusing to delete an allowed root folder itself.")
        shutil.rmtree(p) if p.is_dir() else p.unlink()
        return f"Deleted {p}."

    @registry.tool("Search for files by name under a folder.", obj({"folder": ("string", "Where to look"), "name": ("string", "Part of the file name")}))
    def find_files(folder: str, name: str):
        base = safe_path(folder)
        needle = name.lower()
        hits = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames + dirnames:
                if needle in f.lower():
                    hits.append(str(Path(dirpath) / f))
                    if len(hits) >= 200:
                        return hits
        return hits

    @registry.tool("Report this computer's status: OS, CPU, memory, disk, battery, uptime.", obj({}))
    def system_status():
        info = {"os": f"{platform.system()} {platform.release()}", "machine": platform.machine(), "python": platform.python_version()}
        usage = shutil.disk_usage(Path.home())
        info["disk_free_gb"] = round(usage.free / 1e9, 1)
        try:
            import psutil

            info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            info["memory_used_percent"] = mem.percent
            battery = psutil.sensors_battery()
            if battery:
                info["battery_percent"] = battery.percent
                info["charging"] = battery.power_plugged
            info["uptime_hours"] = round((datetime.now().timestamp() - psutil.boot_time()) / 3600, 1)
        except ImportError:
            info["note"] = "Install psutil for CPU/memory/battery details."
        return info

    @registry.tool(
        "What takes up space on this computer: the biggest folders and files under a folder (default: the user's "
        "home), the drive's free space, and what is safe to empty (Recycle Bin, temporary files, Downloads). Draws a "
        "clickable map and opens it in the browser, so say that the map is on the screen. Takes up to half a minute.",
        obj({"folder?": ("string", "Folder to look at (default: the home folder)"),
             "show?": ("boolean", "Open the map in the browser (default true)")}),
        summarize=lambda a: f"Кое заема място в {a.get('folder') or 'домашната папка'}",
    )
    def disk_space(folder: str | None = None, show: bool = True):
        from .. import diskmap

        root = safe_path(folder) if folder else Path.home()
        found = diskmap.scan(root)
        text = diskmap.summary(found)
        if show:
            path = Path(ctx.settings.home) / "disk-map.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(diskmap.html(found), encoding="utf-8")
            webbrowser.open(path.as_uri())
            text += f"\nThe map is open on the screen ({path})."
        return text

    @registry.tool("Read the clipboard text.", obj({}))
    def get_clipboard():
        import pyperclip

        return pyperclip.paste()

    @registry.tool("Put text on the clipboard.", obj({"text": ("string", "Text")}))
    def set_clipboard(text: str):
        import pyperclip

        pyperclip.copy(text)
        return "Copied to clipboard."

    screen = {"scale": 1.0, "size": None}

    @registry.tool(
        "Read a program's window the way a screen reader does: its buttons, links, fields, tabs and text by name, "
        "numbered. Works in any program and in the owner's own browser (Avast, Chrome, Edge) with their logins "
        "(Windows). Use it, then click_text, to work in a program or on a website: far more reliable than pixels. "
        "Use look_at_screen when this finds nothing useful (pictures, games, canvas).",
        obj({"window?": ("string", "Part of the window title, e.g. 'Avast' (default: the window read last, else the "
                                   "one in front other than Jarvis)")}),
    )
    def read_window(window: str | None = None):
        return winui.read(window)

    @registry.tool(
        "Click a button, link, field, tab or menu item in a program's window by its words (or its number from "
        "read_window), and optionally type into it and press Enter. The mouse goes to the element itself, no "
        "coordinates. Answers with what the window shows right after the click, so no read_window is needed "
        "to check unless a page was still loading.",
        obj({"target": ("string", "The words on the element (as read_window shows them), or its number"),
             "window?": ("string", "Part of the window title (default: the window read last)"),
             "text?": ("string", "Text to type after clicking, e.g. into a field"),
             "enter?": ("boolean", "Press Enter after typing")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Натиска „{a.get('target')}“" + (f" и пише „{a['text']}“" if a.get("text") else ""),
    )
    def click_text(target: str, window: str | None = None, text: str | None = None, enter: bool = False):
        return winui.click(target, window, text, enter)

    def shoot(save_to: str | None = None) -> tuple[bytes, tuple[int, int]]:
        """A screenshot scaled to at most 1280 px wide, as PNG bytes and its size; control_input and
        work_on_screen take coordinates in these pixels."""
        import io

        import pyautogui

        shot = pyautogui.screenshot()
        if save_to:
            target = Path(save_to).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            shot.save(target)
        width, height = shot.size
        scale = min(1.0, 1280 / width)
        screen["scale"] = scale
        small = shot.resize((int(width * scale), int(height * scale))) if scale < 1 else shot
        screen["size"] = small.size
        buf = io.BytesIO()
        small.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), small.size

    @registry.tool(
        "Look at the computer screen: returns a screenshot you can see. For buttons, links and fields use "
        "read_window and click_text first; this with control_input is for what has no words (pictures, games, "
        "maps): look, act, look again to check.",
        obj({"save_to?": ("string", "Also save the full-size PNG here")}),
    )
    def look_at_screen(save_to: str | None = None):
        data, size = shoot(save_to)
        note = (f"Screenshot {size[0]}x{size[1]}. Give control_input coordinates in this image's pixels "
                f"(x 0-{size[0] - 1}, y 0-{size[1] - 1}). To press something with words on it, click_text is "
                "more reliable.")
        return Image(data, "image/png", note)

    def do_input(action: str, x=None, y=None, x2=None, y2=None, text: str = "", keys: list | None = None,
                 amount: int = -5) -> None:
        """One mouse or keyboard action, coordinates in the pixels of the latest screenshot."""
        import pyautogui

        def real(v):
            return None if v is None else int(v / screen["scale"])

        size = screen["size"]
        for px, py in ((x, y), (x2, y2)):
            if size and px is not None and py is not None and not (0 <= px < size[0] and 0 <= py < size[1]):
                raise ValueError(f"({px}, {py}) is outside the screenshot ({size[0]}x{size[1]}). Coordinates are pixels "
                                 "of the latest look_at_screen image (not 0-1000, not the real screen). Look again, or "
                                 "use click_text with the words on the button.")

        X, Y = real(x), real(y)
        if action == "click":
            pyautogui.click(X, Y)
        elif action == "double_click":
            pyautogui.doubleClick(X, Y)
        elif action == "right_click":
            pyautogui.rightClick(X, Y)
        elif action == "move":
            pyautogui.moveTo(X, Y, duration=0.2)
        elif action == "drag":
            pyautogui.moveTo(X, Y)
            pyautogui.dragTo(real(x2), real(y2), duration=0.4)
        elif action == "scroll":
            pyautogui.scroll(amount, X, Y)
        elif action == "type":
            winui.type_text(text)
        elif action == "hotkey":
            pyautogui.hotkey(*(keys or []))
        elif action == "press":
            for key in keys or []:
                pyautogui.press(key)
        elif action == "wait":
            time.sleep(min(10, max(0.5, float(amount if amount and amount > 0 else 2))))
        else:
            raise ValueError(f"Unknown action {action}")

    @registry.tool(
        "Control the mouse and keyboard. Coordinates are pixels in the latest look_at_screen image. "
        "Actions: click, double_click, right_click, move, drag (to x2,y2), scroll (amount, + up / - down), "
        "type (text), hotkey (keys, e.g. ['ctrl','c']), press (keys, one after another).",
        obj({
            "action": ("string", "click | double_click | right_click | move | drag | scroll | type | hotkey | press"),
            "x?": ("integer", "X in the screenshot"),
            "y?": ("integer", "Y in the screenshot"),
            "x2?": ("integer", "Drag target X"),
            "y2?": ("integer", "Drag target Y"),
            "text?": ("string", "Text to type"),
            "keys?": ("array", "Key names"),
            "amount?": ("integer", "Scroll clicks"),
        }),
        confirm=True,
        local=True,
        summarize=lambda a: f"Мишка/клавиатура: {a}",
    )
    def control_input(action: str, x: int | None = None, y: int | None = None, x2: int | None = None,
                      y2: int | None = None, text: str = "", keys: list | None = None, amount: int = -5):
        do_input(action, x, y, x2, y2, text, keys, amount)
        return "Done. Look at the screen to check the result."

    @registry.tool(
        "Take over the screen for a goal that needs many quick looks and moves with no words to click: a game, "
        "a map, a drawing, a video player, a program read_window cannot read. You look at the screen, make one "
        "mouse or keyboard move, look again, and so on, on your own, until the goal is reached, you are stuck, or "
        "the moves run out; every move shows on the live screen. Give a goal that can be seen when it is done "
        "(\"win this round\", \"zoom the map to Sofia\", \"close every pop-up\"). For buttons and links with words "
        "use read_window and click_text instead.",
        obj({"goal": ("string", "What the screen should show at the end, and how to play"),
             "window?": ("string", "Part of the window's title to bring in front first (Windows)"),
             "steps?": ("integer", "At most this many moves (default 20, up to 60)")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Поема екрана: {a.get('goal')}" + (f" (прозорец „{a['window']}“)" if a.get("window") else ""),
    )
    def work_on_screen(goal: str, window: str | None = None, steps: int = 20):
        from .. import screenwork
        from ..brain import TURN

        jarvis = getattr(ctx, "jarvis", None)
        if jarvis is None:
            raise RuntimeError("The brain is not running here.")
        if window and winui.on_windows():
            try:
                with winui._session(lambda auto: winui.bring_to_front(auto, winui.find_window(auto, window))):
                    pass
            except Exception as exc:  # a window that cannot be found or focused: the loop still looks at the screen
                log.info("could not bring %r in front: %s", window, exc)
        turn = TURN.get() or {}
        user = turn.get("user")
        mark = jarvis._mark(user)  # "спри" while the loop runs: it ends at the next move

        def decide(prompt: str, image: bytes) -> str:
            import base64

            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.b64encode(image).decode()}},
                       {"type": "text", "text": prompt}]
            response = jarvis._request([{"role": "user", "content": content}], None, screenwork.SYSTEM, set(),
                                       tools=[], max_tokens=300, effort="low")
            return " ".join(b.text for b in response.content if b.type == "text")

        def act(move: dict) -> None:
            do_input(move["action"], move.get("x"), move.get("y"), move.get("x2"), move.get("y2"),
                     move.get("text") or "", move.get("keys"), move.get("amount") if move.get("amount") is not None else -5)

        return screenwork.run(goal, lambda: shoot(), decide, act, steps=min(60, max(1, int(steps or 20))),
                              stopped=lambda: jarvis._mark(user) != mark, report=turn.get("on_step"))

    @registry.tool(
        "Run Python code on this computer and return what it prints. Good for calculations, data, "
        "spreadsheets, documents and images. Not for the mouse, keyboard, screen or browser: those are "
        "click_text, control_input, read_window, look_at_screen and open_target.",
        obj({"code": ("string", "Python source"), "timeout?": ("integer", "Seconds (default 300)")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Изпълни Python код:\n{a.get('code')}",
    )
    def run_python(code: str, timeout: int = 300):
        refused = browsers.by_hand(code)
        if refused:
            raise RuntimeError(refused)
        return _clip(run_text([sys.executable, "-c", code], timeout=timeout))

    @registry.tool(
        "Set the system volume (0-100) or mute. Works on Windows, macOS and Linux (PulseAudio/PipeWire).",
        obj({"level?": ("integer", "Volume 0-100"), "mute?": ("boolean", "Mute (true) or unmute (false)")}),
    )
    def set_volume(level: int | None = None, mute: bool | None = None):
        if sys.platform == "darwin":
            if level is not None:
                subprocess.run(["osascript", "-e", f"set volume output volume {level}"], check=True)
            if mute is not None:
                subprocess.run(["osascript", "-e", f"set volume output muted {str(mute).lower()}"], check=True)
        elif sys.platform.startswith("win"):
            from ctypes import POINTER, cast

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            device = AudioUtilities.GetSpeakers()
            vol = cast(device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
            if level is not None:
                vol.SetMasterVolumeLevelScalar(level / 100, None)
            if mute is not None:
                vol.SetMute(int(mute), None)
        else:
            if level is not None:
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], check=True)
            if mute is not None:
                subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"], check=True)
        return "Volume set."

    @registry.tool(
        "Media keys on this computer: play/pause, next, previous or stop whatever is playing (Spotify, YouTube "
        "in the browser, any player), volume up/down, mute.",
        obj({"key": ("string", "play_pause | next | previous | stop | volume_up | volume_down | mute"),
             "times?": ("integer", "How many presses, e.g. volume steps (default 1)")}),
    )
    def media_control(key: str, times: int = 1):
        if key not in MEDIA_KEYS:
            raise ValueError(f"Unknown key {key}. Use one of: {', '.join(MEDIA_KEYS)}")
        if sys.platform.startswith("linux") and shutil.which("playerctl") and key in PLAYERCTL:
            subprocess.run(["playerctl", PLAYERCTL[key]], check=False)
        else:
            import pyautogui

            for _ in range(max(1, min(int(times or 1), 50))):
                pyautogui.press(MEDIA_KEYS[key])
        return "Done."

    @registry.tool("List the programs that have open windows on this computer.", obj({}))
    def open_programs():
        if sys.platform.startswith("win"):
            script = ("Get-Process | Where-Object {$_.MainWindowTitle} | Sort-Object ProcessName | "
                      "ForEach-Object { $_.ProcessName + ' | ' + $_.MainWindowTitle }")
            return _clip(run_text(["powershell", "-NoProfile", "-Command", script], timeout=30))
        if sys.platform == "darwin":
            script = 'tell application "System Events" to get name of (processes where background only is false)'
            return _clip(run_text(["osascript", "-e", script], timeout=30))
        if shutil.which("wmctrl"):
            return _clip(run_text(["wmctrl", "-l"], timeout=30))
        return _clip(run_text(["ps", "-eo", "comm", "--sort=comm"], timeout=30))

    @registry.tool(
        "Close a program on this computer by its name (e.g. 'chrome', 'spotify', 'notepad'; see open_programs).",
        obj({"name": ("string", "Program name"), "force?": ("boolean", "Kill it even if it does not want to close")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Затвори програмата {a.get('name')}" + (" (насила)" if a.get("force") else ""),
    )
    def close_program(name: str, force: bool = False):
        name = name.strip()
        if sys.platform.startswith("win"):
            exe = name if name.lower().endswith(".exe") else name + ".exe"
            return run_text(["taskkill", "/IM", exe, "/T", *(["/F"] if force else [])], timeout=30)
        if sys.platform == "darwin" and not force:
            return run_text(["osascript", "-e", f'quit app "{name}"'], timeout=30)
        return run_text(["pkill", *(["-9"] if force else []), "-i", "-f", name], timeout=30)

    @registry.tool(
        "Lock this computer's screen, or put it to sleep.",
        obj({"action": ("string", "lock | sleep")}),
    )
    def lock_computer(action: str = "lock"):
        run_capture(POWER[_platform()][action], timeout=30)
        return "Locked." if action == "lock" else "Going to sleep."

    @registry.tool(
        "Restart or shut down this computer (in 10 seconds), or cancel a planned shutdown.",
        obj({"action": ("string", "restart | shutdown | cancel")}),
        confirm=True,
        local=True,
        summarize=lambda a: {"restart": "РЕСТАРТИРАЙ компютъра", "shutdown": "ИЗКЛЮЧИ компютъра"}.get(
            a.get("action"), "Отмени изключването"),
    )
    def power_off(action: str):
        run_capture(POWER[_platform()][action], timeout=30)
        return {"restart": "Restarting in 10 seconds.", "shutdown": "Shutting down in 10 seconds."}.get(action, "Cancelled.")


MEDIA_KEYS = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "stop": "stop",
              "volume_up": "volumeup", "volume_down": "volumedown", "mute": "volumemute"}
PLAYERCTL = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}
POWER = {
    "windows": {"lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
                "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                "restart": ["shutdown", "/r", "/t", "10"], "shutdown": ["shutdown", "/s", "/t", "10"],
                "cancel": ["shutdown", "/a"]},
    "mac": {"lock": ["pmset", "displaysleepnow"], "sleep": ["pmset", "sleepnow"],
            "restart": ["osascript", "-e", 'tell app "System Events" to restart'],
            "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'], "cancel": ["true"]},
    "linux": {"lock": ["loginctl", "lock-session"], "sleep": ["systemctl", "suspend"],
              "restart": ["shutdown", "-r", "+0"], "shutdown": ["shutdown", "-h", "+0"], "cancel": ["shutdown", "-c"]},
}


def _platform() -> str:
    return "windows" if sys.platform.startswith("win") else "mac" if sys.platform == "darwin" else "linux"
