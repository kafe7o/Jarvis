"""What takes up space on this computer (from the owner's video: "a live map of your drive is on your screen").

``scan`` walks a folder for a bounded time and returns the biggest folders and files, the drive's totals and the
places that are safe to empty (Recycle Bin, temporary files, Downloads). ``html`` draws it as a treemap the
browser shows; the folders can be clicked to open them."""

from __future__ import annotations

import html as html_lib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

GB = 1e9
BUDGET = 25.0  # seconds of walking at most: the answer must come while the owner waits
TOP_FOLDERS = 14
TOP_FILES = 12
BIG_FILE = 200e6


def scan(root: str | Path | None = None, budget: float = BUDGET, depth: int = 3) -> dict:
    root = Path(root or Path.home()).expanduser()
    deadline = time.monotonic() + budget
    sizes: dict[str, int] = {}  # folder (up to ``depth`` below root) -> bytes, children included
    files: list[tuple[int, str]] = []
    partial = False

    def walk(folder: Path, level: int) -> int:
        nonlocal partial
        total = 0
        if time.monotonic() > deadline:
            partial = True
            return 0
        try:
            entries = list(os.scandir(folder))
        except (PermissionError, OSError):
            return 0
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    total += walk(Path(entry.path), level + 1)
                else:
                    size = entry.stat(follow_symlinks=False).st_size
                    total += size
                    if size >= BIG_FILE:
                        files.append((size, entry.path))
            except (PermissionError, OSError):
                continue
        if level <= depth:
            sizes[str(folder)] = total
        return total

    total = walk(root, 0)
    folders = sorted(((s, p) for p, s in sizes.items() if p != str(root) and s > 0), key=lambda sp: (-sp[0], sp[1]))
    # The biggest folders that are not just a parent of another big one: a parent counts when its own files or
    # the rest of its children hold most of its size.
    chosen: list[tuple[int, str]] = []
    for size, path in folders:  # parents come first (they are at least as big as their children)
        above = [c for c in chosen if path.startswith(c[1] + os.sep)]
        if above and size > 0.85 * above[-1][0]:
            chosen.remove(above[-1])  # the child holds nearly all of it: show the child, it says more
        elif len(chosen) >= TOP_FOLDERS:
            continue
        chosen.append((size, path))
    chosen.sort(reverse=True)
    files.sort(reverse=True)
    try:
        usage = shutil.disk_usage(root)
        drive = {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError:
        drive = {"total": 0, "used": 0, "free": 0}
    return {"root": str(root), "total": total, "partial": partial, "drive": drive,
            "folders": chosen, "files": files[:TOP_FILES], "cleanup": cleanup()}


def cleanup() -> list[dict]:
    """Places that are safe to empty, with their size: temporary files, Downloads, the Recycle Bin."""
    out = []
    spots = [("Временни файлове", Path(tempfile.gettempdir())), ("Downloads", Path.home() / "Downloads")]
    if sys.platform.startswith("win"):
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        spots.append(("Кошче", drive / "$Recycle.Bin"))
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        if local.name:
            spots.append(("Кеш на Windows", local / "Temp"))
    for name, path in spots:
        if not path.exists():
            continue
        size = folder_size(path, 4.0)
        out.append({"name": name, "path": str(path), "bytes": size})
    return out


def folder_size(path: Path, budget: float) -> int:
    deadline = time.monotonic() + budget
    total = 0
    for base, _dirs, names in os.walk(path, onerror=lambda e: None):
        if time.monotonic() > deadline:
            break
        for name in names:
            try:
                total += os.stat(os.path.join(base, name), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def gb(n: int) -> str:
    return f"{n / GB:.1f} GB" if n >= GB else f"{n / 1e6:.0f} MB"


def summary(found: dict) -> str:
    d = found["drive"]
    lines = [f"Drive: {gb(d['used'])} used of {gb(d['total'])}, {gb(d['free'])} free"
             + (" (nearly full!)" if d["total"] and d["free"] / d["total"] < 0.1 else "") + "."]
    lines.append(f"{found['root']}: {gb(found['total'])}" + (" (scan cut short; the biggest parts are counted)" if found["partial"] else ""))
    lines += ["Biggest folders:"] + [f"- {gb(s)}  {p}" for s, p in found["folders"]]
    if found["files"]:
        lines += ["Biggest files:"] + [f"- {gb(s)}  {p}" for s, p in found["files"]]
    if found["cleanup"]:
        lines += ["Safe to empty:"] + [f"- {c['name']}: {gb(c['bytes'])}  ({c['path']})" for c in found["cleanup"] if c["bytes"]]
    return "\n".join(lines)


def html(found: dict) -> str:
    """A treemap of the biggest folders (squarified by rows), plus the lists; standalone, no scripts needed."""
    items = found["folders"][:TOP_FOLDERS]
    total = sum(s for s, _p in items) or 1
    rows = []
    # simple slice-and-dice treemap: rows of two or three boxes, each row's height by its share
    i, y = 0, 0.0
    while i < len(items):
        group = items[i:i + (3 if i else 2)]
        share = sum(s for s, _p in group) / total
        x = 0.0
        for size, path in group:
            w = size / sum(s for s, _p in group)
            name = html_lib.escape(os.path.basename(path.rstrip("\\/")) or path)
            rows.append(f'<a class="box" href="file:///{html_lib.escape(path).replace(chr(92), "/")}" '
                        f'style="left:{x * 100:.2f}%;top:{y * 100:.2f}%;width:{w * 100:.2f}%;height:{share * 100:.2f}%" '
                        f'title="{html_lib.escape(path)}"><b>{name}</b><span>{gb(size)}</span></a>')
            x += w
        y += share
        i += len(group)
    d = found["drive"]
    used = (d["used"] / d["total"] * 100) if d["total"] else 0
    files = "".join(f"<li>{gb(s)} <code>{html_lib.escape(p)}</code></li>" for s, p in found["files"])
    clean = "".join(f"<li>{html_lib.escape(c['name'])}: <b>{gb(c['bytes'])}</b> <code>{html_lib.escape(c['path'])}</code></li>"
                    for c in found["cleanup"] if c["bytes"])
    return f"""<!doctype html><html lang="bg"><head><meta charset="utf-8"><title>Какво заема място</title>
<style>
body{{margin:0;background:#0b1220;color:#e6edf7;font:15px system-ui,sans-serif}}
header{{padding:16px 24px;border-bottom:1px solid #1f2a3d}} h1{{margin:0 0 6px;font-size:20px}}
.bar{{height:10px;background:#1f2a3d;border-radius:5px;overflow:hidden;max-width:520px}} .bar i{{display:block;height:100%;background:linear-gradient(90deg,#4fd1ff,#ff6fb5);width:{used:.1f}%}}
.map{{position:relative;height:62vh;margin:16px 24px;border:1px solid #1f2a3d;border-radius:12px;overflow:hidden}}
.box{{position:absolute;box-sizing:border-box;border:1px solid #0b1220;background:#17304f;color:inherit;text-decoration:none;padding:8px;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;transition:background .15s}}
.box:hover{{background:#1f4a7a}} .box b{{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .box span{{font-size:12px;color:#9fb3cc}}
section{{padding:0 24px 24px}} ul{{padding-left:18px}} code{{color:#9fb3cc;font-size:12px}}
</style></head><body>
<header><h1>Какво заема място: {html_lib.escape(found['root'])}</h1>
<div>Дискът: {gb(d['used'])} заети от {gb(d['total'])}, свободни {gb(d['free'])}</div><div class="bar"><i></i></div></header>
<div class="map">{''.join(rows)}</div>
<section><h3>Най-големи файлове</h3><ul>{files or '<li>няма над 200 MB</li>'}</ul>
<h3>Безопасно за чистене</h3><ul>{clean or '<li>нищо голямо</li>'}</ul></section></body></html>"""
