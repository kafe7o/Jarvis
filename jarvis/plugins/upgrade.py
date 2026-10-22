"""Jarvis upgrading himself when the owner asks: reads his own code, changes it with a backup and a check,
undoes an upgrade, and installs the newest version from GitHub (see upgrades.py)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import threading

from .. import recipes, upgrades
from ..tools import ToolRegistry, obj

# Where things are, so the model finds the right file without reading everything.
MAP = {
    "jarvis/brain.py": "the brain: PERSONA (your instructions and personality) and the tool loop",
    "jarvis/local.py": "how you think: the models in Ollama on this computer, which one takes each step",
    "jarvis/localmodels.py": "Settings > Мозък: installing Ollama, downloading and adding models, the slider",
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

    def owner_only() -> None:
        from ..brain import TURN

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner's Jarvis learns lessons.")

    @registry.tool(
        "Write down a lesson for next time in LESSONS.md (it goes with every request from now on): when the owner "
        "corrects you, when something failed and you found what works, or a better way to do a recurring task. "
        "One concrete line in Bulgarian, e.g. \"Когато ..., ...\".",
        obj({"lesson": ("string", "The lesson, one line")}),
    )
    def learn(lesson: str):
        from .. import core_files

        owner_only()
        if not core_files.learn(ctx.settings.home, lesson):
            return "Already among your lessons (or too short); nothing added."
        return "Lesson kept. You will follow it from now on."

    @registry.tool(
        "Look at yourself to get better: the steps that failed lately (from the activity log), which tools fail "
        "most, your lessons and your upgrades. Use it when the owner asks you to improve yourself, then write "
        "lessons with learn and, if a code change would clearly help, propose it with upgrade_self.",
        obj({"days?": ("integer", "How many days back (default 7)")}),
    )
    def review_myself(days: int = 7):
        from datetime import datetime, timedelta

        from .. import core_files

        owner_only()
        since = (datetime.now() - timedelta(days=max(1, int(days)))).replace(microsecond=0).isoformat()
        rows = ctx.store.query("SELECT tool, summary, ok, created FROM activity WHERE created >= ? ORDER BY id DESC "
                               "LIMIT 400", (since,))
        failed = [r for r in rows if not r["ok"]]
        counts: dict[str, list[int]] = {}
        for r in rows:
            counts.setdefault(r["tool"], [0, 0])[0 if r["ok"] else 1] += 1
        worst = sorted(((bad, good, tool) for tool, (good, bad) in counts.items() if bad), reverse=True)[:10]
        lines = [f"Steps in the last {days} days: {len(rows)}, failed: {len(failed)}."]
        if worst:
            lines.append("Tools that failed most: " + ", ".join(f"{t} ({b} failed of {b + g})" for b, g, t in worst))
        lines += ["Latest failed steps:"] + [f"- {r['created']} {r['tool']}: {r['summary'][:200]}" for r in failed[:25]]
        lines.append("Your lessons (LESSONS.md):\n" + (core_files.read(ctx.settings.home, "LESSONS.md") or "(none yet)"))
        lines.append("Your recipes (ways that worked, shown to you again on similar tasks):\n" + recipes.listing(ctx.store))
        made = upgrades.records(ctx.settings.home)
        lines.append("Your upgrades: " + ("; ".join(upgrades.describe(r) for r in made[-5:]) or "none yet"))
        return "\n".join(lines)

    @registry.tool(
        "Audit yourself (the optimizer): check everything you keep for contradictions and rot: remembered facts "
        "(duplicates, several facts on one topic), lessons, stale plans, contacts sharing a number, skills that no "
        "longer load, and the failures of the last days. Exact duplicates are removed at once (safe); every "
        "conflict is listed for you to resolve: decide from the dates and the conversation which fact is current "
        "and forget the stale one, close finished plans, and leave anything about people, money or permissions "
        "to the owner. The report is saved in the vault (output/) when there is one.",
        obj({"days?": ("integer", "How many days of failures to look at (default 7)")}),
    )
    def audit_myself(days: int = 7):
        from .. import optimize

        owner_only()
        found = optimize.audit(ctx.store, ctx.settings.home, days=int(days or 7))
        text = optimize.report(found)
        try:
            folder = Path(ctx.settings.vault).expanduser() / "output"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"audit-{datetime.now():%Y-%m-%d}.md"
            path.write_text(text + "\n", encoding="utf-8")
            text += f"\n\n(Saved as {path})"
        except Exception as exc:  # no vault: the report is only returned
            text += f"\n\n(Not saved to the vault: {exc})"
        return text

    @registry.tool(
        "The doctor: when something of yours is not working (a brain that does not answer, the phone, the voice, "
        "the browser, a scheduled job, \"Няма връзка\"), check everything at once and say in the owner's words what "
        "works, what does not and how to fix it: Ollama and the models on this computer, the desktop, voice and "
        "browser packages, the phone over adb, the folders and free space, the scheduled jobs (and ones that run "
        "often for nothing), the failures of the last day, and the version. Nothing is changed and no key is shown. "
        "Then fix what you can yourself (start Ollama, connect the phone, delete a useless job) and tell the owner "
        "only what needs them.",
        obj({}),
    )
    def run_doctor():
        from .. import doctor

        owner_only()
        return doctor.report(doctor.examine(ctx))

    @registry.tool(
        "What you did lately, with numbers: requests, steps, tasks done, reminders and jobs fired, messages answered, "
        "specialists that worked, failures. Use it at the start of the morning brief and whenever the owner asks "
        "what you have been doing.",
        obj({"days?": ("integer", "How many days back (default 1)")}),
    )
    def work_report(days: int = 1):
        from .. import optimize

        return optimize.work_summary(ctx.store, int(days or 1))

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

