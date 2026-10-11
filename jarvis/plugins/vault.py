"""The notes vault (the memory layer from the owner's video): plain Markdown files in three folders, which the
owner can also open as a vault in Obsidian (free).

raw/     the inbox: anything dropped in (notes, links, articles, ideas), by the owner or by Jarvis
wiki/    what Jarvis keeps organised, one note per topic in topic folders, listed in wiki/_master-index.md
output/  answers and reports (morning brief, plans, research, weekly reviews)

No database: Jarvis finds things by searching the text, like the video's "no heavy vector database".
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from ..tools import ToolRegistry, obj

FOLDERS = {
    "raw": "Входяща кутия: пусни тук каквото и да е (бележки, линкове, статии, идеи).",
    "wiki": "Подредено знание: Jarvis го поддържа, по една бележка на тема, в папки по теми.",
    "output": "Отговори и доклади: брифинги, планове, проучвания, седмични прегледи.",
}
INDEX = "wiki/_master-index.md"
TEXT = {".md", ".txt"}
MAX_READ = 20000


def ensure(root: Path) -> Path:
    """Create the vault the first time: the three folders, a master index and a short README."""
    root.mkdir(parents=True, exist_ok=True)
    for name in FOLDERS:
        (root / name).mkdir(exist_ok=True)
    index = root / INDEX
    if not index.exists():
        index.write_text("# Главен индекс\n\nТемите в wiki/, по една на ред: [[тема/бележка]] - за какво е.\n", encoding="utf-8")
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text("# Jarvis Vault\n\n" + "\n".join(f"- **{k}/** {v}" for k, v in FOLDERS.items())
                          + "\n\nОтвори папката в Obsidian (безплатно, obsidian.md): Open folder as vault.\n", encoding="utf-8")
    return root


def inside(root: Path, path: str) -> Path:
    """``path`` (relative to the vault) as a real path, refusing anything outside the vault."""
    root = root.resolve()
    target = (root / (path or "").strip().lstrip("/\\")).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"{path} is outside the notes vault ({root}).")
    return target


def file_name(title: str) -> str:
    """A safe file name (Windows too) from a note's title; Cyrillic stays."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:80] or "бележка"


def search(root: Path, query: str, folder: str | None = None, limit: int = 8) -> list[dict]:
    """Notes that contain the query's words, best first, with the lines that match."""
    words = [w for w in re.findall(r"\w+", (query or "").casefold()) if len(w) > 1]
    if not words:
        return []
    base = inside(root, folder) if folder else root
    hits = []
    for path in sorted(base.rglob("*"))[:3000]:
        if path.suffix.lower() not in TEXT or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low, name = text.casefold(), path.stem.casefold()
        score = sum(low.count(w) + 3 * name.count(w) for w in words)
        if not score:
            continue
        lines = [line.strip()[:200] for line in text.splitlines() if any(w in line.casefold() for w in words)][:3]
        hits.append({"path": path.relative_to(root).as_posix(), "score": score, "lines": lines})
    return sorted(hits, key=lambda h: -h["score"])[:limit]


def register(registry: ToolRegistry, ctx) -> None:
    def root() -> Path:  # read each time, so a new folder in Settings applies at once
        return ensure(Path(ctx.settings.vault).expanduser())

    @registry.tool(
        "Save a new note in the notes vault: to raw/ (the inbox: anything worth keeping, e.g. something the owner "
        "says to note down, a link, an idea) or to output/ (a finished answer or report: brief, plan, research). "
        "Use vault_write to organise the wiki.",
        obj({"text": ("string", "The note, in Markdown"), "title?": ("string", "Short title (becomes the file name)"),
             "folder?": ("string", "raw (default) or output")}),
    )
    def vault_note(text: str, title: str | None = None, folder: str = "raw"):
        if folder not in ("raw", "output"):
            raise ValueError("folder must be raw or output; use vault_write for the wiki.")
        stamp = datetime.now().strftime("%Y-%m-%d %H%M")
        heading = title or (text.strip().splitlines()[0].lstrip("# ") if text.strip() else "")
        path = inside(root(), f"{folder}/{stamp} {file_name(heading)}.md")
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return f"Saved {path.relative_to(root().resolve()).as_posix()} in the vault."

    @registry.tool(
        "Write or update a note in the notes vault, e.g. wiki/ai-agents/claude-code.md, or the index "
        "wiki/_master-index.md. Replaces the note unless append is true.",
        obj({"path": ("string", "Path inside the vault, ending in .md"), "text": ("string", "Markdown"),
             "append?": ("boolean", "Add to the end instead of replacing")}),
    )
    def vault_write(path: str, text: str, append: bool = False):
        target = inside(root(), path)
        if target.suffix.lower() != ".md":
            raise ValueError("Vault notes end in .md.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if append and target.exists():
            text = target.read_text(encoding="utf-8").rstrip() + "\n\n" + text
        target.write_text(text.rstrip() + "\n", encoding="utf-8")
        return f"Wrote {target.relative_to(root().resolve()).as_posix()}."

    @registry.tool("Read a note from the notes vault.", obj({"path": ("string", "Path inside the vault")}))
    def vault_read(path: str):
        text = inside(root(), path).read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= MAX_READ else text[:MAX_READ] + "\n[... cut]"

    @registry.tool(
        "Search the notes vault for words (no AI, plain text search): returns the best notes and matching lines.",
        obj({"query": ("string", "Words to look for"), "folder?": ("string", "raw, wiki or output (default: everywhere)")}),
    )
    def vault_search(query: str, folder: str | None = None):
        hits = search(root(), query, folder)
        if not hits:
            return f"Nothing about '{query}' in the vault."
        return "\n".join(f"- {h['path']}: " + " | ".join(h["lines"]) for h in hits)

    @registry.tool(
        "List the notes in the notes vault (or one folder of it), with where the vault is on this computer.",
        obj({"folder?": ("string", "raw, wiki, output or a subfolder (default: everything)")}),
    )
    def vault_list(folder: str | None = None):
        base = inside(root(), folder) if folder else root().resolve()
        files = [p.relative_to(root().resolve()).as_posix() for p in sorted(base.rglob("*"))
                 if p.is_file() and p.suffix.lower() in TEXT]
        listing = "\n".join(files[:300]) or "(empty)"
        return f"Vault: {root().resolve()}\n{listing}" + (f"\n... and {len(files) - 300} more" if len(files) > 300 else "")

    @registry.tool(
        "Mark a raw/ note as filed into the wiki: moves it to raw/_done/ so it is not organised twice.",
        obj({"path": ("string", "The raw note, e.g. raw/2026-10-08 0930 idea.md")}),
    )
    def vault_done(path: str):
        source = inside(root(), path)
        raw = inside(root(), "raw")
        if raw not in source.parents or "_done" in source.parts:
            raise ValueError("Only notes in raw/ can be marked as filed.")
        done = raw / "_done"
        done.mkdir(exist_ok=True)
        shutil.move(str(source), str(done / source.name))
        return f"Moved to raw/_done/{source.name}."
