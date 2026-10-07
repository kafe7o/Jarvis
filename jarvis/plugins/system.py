"""Control of the computer: shell, programs, files, clipboard, keyboard/mouse, screenshots."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from ..tools import Image, ToolRegistry, obj

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


def register(registry: ToolRegistry, ctx) -> None:
    roots = [Path(r).expanduser().resolve() for r in ctx.settings.allowed_roots]

    def safe_path(path: str) -> Path:
        p = Path(path).expanduser().resolve()
        if roots and not any(p == r or r in p.parents for r in roots):
            raise PermissionError(f"{p} is outside the allowed folders ({', '.join(map(str, roots))}). Set JARVIS_FILE_ROOTS.")
        return p

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
        proc = subprocess.run(
            command, shell=True, cwd=cwd and str(Path(cwd).expanduser()), capture_output=True, text=True, timeout=timeout
        )
        return _clip(f"exit code {proc.returncode}\n{proc.stdout}{proc.stderr}")

    @registry.tool(
        "Open a website, file, folder or program with the system default handler "
        "(e.g. 'https://youtube.com', '~/Documents/report.pdf', 'spotify', 'calc').",
        obj({"target": ("string", "URL, path or program name")}),
    )
    def open_target(target: str):
        if "://" in target or target.startswith("www."):
            webbrowser.open(target if "://" in target else "https://" + target)
            return f"Opened {target} in the browser."
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

    @registry.tool("Read the clipboard text.", obj({}))
    def get_clipboard():
        import pyperclip

        return pyperclip.paste()

    @registry.tool("Put text on the clipboard.", obj({"text": ("string", "Text")}))
    def set_clipboard(text: str):
        import pyperclip

        pyperclip.copy(text)
        return "Copied to clipboard."

    screen = {"scale": 1.0}

    @registry.tool(
        "Look at the computer screen: returns a screenshot you can see. Use it to operate any program or "
        "website visually together with control_input (look, act, look again to check).",
        obj({"save_to?": ("string", "Also save the full-size PNG here")}),
    )
    def look_at_screen(save_to: str | None = None):
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
        buf = io.BytesIO()
        small.convert("RGB").save(buf, format="PNG", optimize=True)
        note = f"Screenshot {small.size[0]}x{small.size[1]}. Give control_input coordinates in this image's pixels."
        return Image(buf.getvalue(), "image/png", note)

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
        import pyautogui

        def real(v):
            return None if v is None else int(v / screen["scale"])

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
            try:  # pyautogui.write only handles ASCII; paste anything else (e.g. Cyrillic)
                text.encode("ascii")
                pyautogui.write(text, interval=0.02)
            except UnicodeEncodeError:
                import pyperclip

                pyperclip.copy(text)
                pyautogui.hotkey("command" if sys.platform == "darwin" else "ctrl", "v")
        elif action == "hotkey":
            pyautogui.hotkey(*(keys or []))
        elif action == "press":
            for key in keys or []:
                pyautogui.press(key)
        else:
            raise ValueError(f"Unknown action {action}")
        return "Done. Look at the screen to check the result."

    @registry.tool(
        "Run Python code on this computer and return what it prints. Good for calculations, data, "
        "spreadsheets, documents, images and quick automation.",
        obj({"code": ("string", "Python source"), "timeout?": ("integer", "Seconds (default 300)")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Изпълни Python код:\n{a.get('code')}",
    )
    def run_python(code: str, timeout: int = 300):
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout)
        return _clip(f"exit code {proc.returncode}\n{proc.stdout}{proc.stderr}")

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
