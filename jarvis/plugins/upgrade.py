"""Jarvis upgrading himself when the owner asks: reads his own code, changes it with a backup and a check,
undoes an upgrade, and installs the newest version from GitHub (see upgrades.py)."""

from __future__ import annotations

import threading

from .. import upgrades
from ..tools import ToolRegistry, obj

# Where things are, so the model finds the right file without reading everything.
MAP = {
    "jarvis/brain.py": "the brain: PERSONA (your instructions and personality) and the tool loop",
    "jarvis/router.py": "the three levels: commands answered without AI (rules), quick model, full agent",
    "jarvis/hub.py": "the app's server: API, accounts, live events, settings (EXTRA_SETTINGS)",
    "jarvis/web/index.html": "the whole app screen (HTML, CSS, JavaScript), including Jarvis mode (hud*), На живо, Памет",
    "jarvis/plugins/": "your tools by group (memory, tasks, system, android, comms, browser, ...)",
    "jarvis/tts.py": "your voice", "jarvis/voice/": "listening and the wake word",
    "jarvis/config.py": "settings read from .env", "jarvis/routines.py": "routines on a schedule",
}
SHOW = 60_000
UPGRADE = obj({
    "what": ("string", "What the upgrade does, in the owner's language, one sentence"),
    "changes": ("array", "Edits: {path, find, replace} to change a file, or {path, content} for a new file"),
})
UPGRADE["properties"]["changes"]["items"] = {
    "type": "object", "required": ["path"],
    "properties": {"path": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"},
                   "content": {"type": "string"}},
}


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Read your own source code (the Jarvis program on this computer) before changing it with upgrade_self. "
        "Without path: the list of files and what the main ones do. With path: the file. With find: only the "
        "parts of the file around that text (use it for big files such as jarvis/web/index.html).",
        obj({"path?": ("string", "File, e.g. jarvis/brain.py"), "find?": ("string", "Text to look for in it")}),
    )
    def read_own_code(path: str = "", find: str = ""):
        if not path:
            root = upgrades.source_root()
            files = sorted(p for p in (root / "jarvis").rglob("*") if p.is_file()
                           and "__pycache__" not in p.parts and p.suffix in upgrades.EDITABLE)
            listing = "\n".join(f"{p.relative_to(root).as_posix()} ({p.stat().st_size // 1024 + 1} KB)" for p in files)
            return "Main parts:\n" + "\n".join(f"- {k}: {v}" for k, v in MAP.items()) + "\n\nFiles:\n" + listing
        rel = upgrades.safe_path(path)
        text = upgrades.read(upgrades.source_root(), rel)
        if text is None:
            return f"There is no {rel}."
        if not find:
            return text if len(text) <= SHOW else text[:SHOW] + f"\n… ({len(text) - SHOW} more characters: read parts with find)"
        lines, parts, last = text.splitlines(), [], -1
        hits = [i for i, line in enumerate(lines) if find.casefold() in line.casefold()][:8]
        for i in hits:
            start, end = max(i - 30, last + 1), min(i + 40, len(lines))
            if start < end:
                parts.append(f"--- {rel}, lines {start + 1}-{end} ---\n" + "\n".join(lines[start:end]))
                last = end - 1
        return "\n".join(parts) if parts else f"„{find}“ is not in {rel}."

    def preview(args: dict) -> str:
        lines = [f"Надстройка на Jarvis: {args.get('what', '')}"]
        for change in args.get("changes") or []:
            new = change.get("content") if change.get("content") is not None else change.get("replace", "")
            lines.append(f"\n{change.get('path', '')}:\n{str(new)[:400]}" + ("…" if len(str(new)) > 400 else ""))
        text = "\n".join(lines)
        return text[:2000] + ("…" if len(text) > 2000 else "")

    @registry.tool(
        "Change your own code when the owner asks you to upgrade, improve or change yourself (how you behave, "
        "what you say, your screens, a tool that should work differently). First read the files with "
        "read_own_code. Each change is an exact edit: path + find (text copied exactly from the file, found once) "
        "+ replace, or path + content for a new file. A copy of the old files is kept, the new code is checked "
        "(it must compile, import and load every tool) and put back by itself if the check fails. The owner sees "
        "the change and says yes first. Afterwards you "
        "restart by yourself right after your answer. For a separate new ability create_skill is simpler.",
        UPGRADE,
        confirm=True,  # always the owner's „да“ with the change shown, even when other actions are always allowed
        summarize=preview,
    )
    def upgrade_self(what: str, changes: list):
        record = upgrades.apply(ctx.settings.home, what, changes)
        if record["state"] == "failed":
            return ("The new code did not pass the check, so the old files are back and nothing changed. "
                    f"Fix it and call upgrade_self again:\n{record['error']}")
        return (f"Upgrade {record['id']} is in ({', '.join(record['files'])}). {restart_soon()} "
                f"To undo it: undo_upgrade (or the owner says „върни надстройката“).")

    @registry.tool(
        "Undo one of your upgrades (the last one by default): the files from before it come back and you restart.",
        obj({"upgrade_id?": ("integer", "Number from list_upgrades")}),
    )
    def undo_upgrade(upgrade_id: int | None = None):
        record = upgrades.undo(ctx.settings.home, upgrade_id)
        return f"Върнах надстройка {record['id']} („{record['what']}“). {restart_soon(bulgarian=True)}"

    @registry.tool("List the upgrades you have made to yourself, newest first.", obj({}))
    def list_upgrades():
        made = upgrades.records(ctx.settings.home)
        return "\n".join(upgrades.describe(r) for r in reversed(made)) or "Още не съм се надстройвал."

    @registry.tool(
        "Install the newest version of yourself (Jarvis) from GitHub and restart. Use when the owner asks you "
        "to update yourself. Your own upgrades are applied again after it.",
        obj({}),
    )
    def update_jarvis():
        from .. import updater

        hub = getattr(ctx, "hub", None)
        if hub is None:
            return "Тук приложението не работи: в папката на Jarvis пусни `jarvis update`."
        if updater.newer(updater.project_root()) is None:
            return "Вече съм с последната версия."
        threading.Thread(target=hub.update_now, kwargs={"wait_idle": True}, name="jarvis-update", daemon=True).start()
        return "Има нова версия. Инсталирам я и се рестартирам веднага щом свърша с този отговор."

    def restart_soon(bulgarian: bool = False) -> str:
        hub = getattr(ctx, "hub", None)
        if hub is not None and getattr(hub, "restartable", False):
            hub.restart_when_idle()
            return "Рестартирам се след този отговор." if bulgarian else "You restart by yourself right after this answer."
        return "Рестартирай Jarvis, за да заработи." if bulgarian else "Ask the owner to restart Jarvis for it to work."

