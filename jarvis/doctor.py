"""The doctor: when something of Jarvis's is not working, one look at everything, in the owner's words.

"Something breaks? Run the doctor." The owner is not technical, so each finding is one line: what it is,
whether it works, and what to do about it. The checks are cheap and safe (no key is printed, nothing is
changed): the brain on this computer (Ollama and its models), the
desktop, voice and browser packages, the phone over adb, the vault and the data folder, free disk space,
the scheduled jobs (and ones that keep the laptop busy for nothing), the failures of the last day, and
the installed version. ``run_doctor`` is the tool; ``jarvis doctor`` prints the same in PowerShell.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import browsers

log = logging.getLogger("jarvis.doctor")

OK, WARN, BAD = "ok", "warn", "bad"
MARK = {OK: "✅", WARN: "⚠️", BAD: "❌"}
BUSY_JOB = ("самоусъвърш", "self-improv", "self improv", "итераци", "iteration", "подобри се", "improve yourself")
PACKAGES = [  # (what, module, which extra installs it, needed on)
    ("Екран и клавиатура", "pyautogui", "desktop", None),
    ("Четене на прозорци (UI Automation)", "uiautomation", "desktop", "win32"),
    ("Браузър (Playwright)", "playwright", "browser", None),
    ("Микрофон", "sounddevice", "voice", None),
    ("Разпознаване на говор (Whisper)", "faster_whisper", "voice", None),
    ("Глас (edge-tts)", "edge_tts", "voice", None),
    ("Процеси и памет (psutil)", "psutil", "desktop", None),
]


def finding(state: str, what: str, hint: str = "") -> dict:
    return {"state": state, "what": what, "hint": hint}


def brains(ctx) -> list[dict]:
    """The brain on this computer: is Ollama installed and running, which model thinks, which takes the plain
    answers, and is there one that finds memories by meaning."""
    from . import local, localmodels

    try:
        running = localmodels.version()
    except Exception:
        running = ""
    if not running:
        if localmodels.ollama_program():
            return [finding(BAD, "Мозък (Ollama)", "инсталиран е, но не е пуснат: Настройки > Мозък > „Пусни го“")]
        return [finding(BAD, "Мозък (Ollama)", "не е инсталиран: Настройки > Мозък > „Инсталирай Ollama“")]
    fixed = getattr(ctx.settings, "local_model", "") or None
    chosen = local.pick(fixed)
    if not chosen:
        return [finding(BAD, "Мозък", f"Ollama {running} работи, но няма модел: изтегли от Настройки > Мозък")]
    quick = None if fixed else local.quick_partner(chosen)  # a model picked on the slider takes every step
    out = [finding(OK, "Мозък", f"мисли с {chosen} на този компютър (Ollama {running})"
                   + (f"; кратките отговори на {quick}" if quick else ""))]
    if not fixed and not quick and local.QWEN35.search(chosen) and "9b" in chosen.lower():
        out.append(finding(WARN, "Бърз модел", "няма 4B до 9B: с Qwen 3.5 4B кратките отговори стават няколко пъти по-бързо"))
    if not local.embedder():
        out.append(finding(WARN, "Памет по смисъл", "няма малкия модел за търсене по смисъл (Настройки > Мозък)"))
    return out


def packages() -> list[dict]:
    out = []
    for what, module, extra, platform_only in PACKAGES:
        if platform_only and not sys.platform.startswith(platform_only):
            continue
        try:
            __import__(module)
            out.append(finding(OK, what))
        except Exception:
            out.append(finding(WARN, what, f"не е инсталирано: в PowerShell, в папката на Jarvis: pip install -e .[{extra}]"))
    return out


def phone() -> list[dict]:
    from .plugins import android

    if not shutil.which("adb"):
        return [finding(WARN, "Телефон (adb)", "adb не е инсталиран; android_wireless го обяснява стъпка по стъпка")]
    try:
        serials = android.connected()
    except Exception as exc:
        return [finding(BAD, "Телефон (adb)", f"adb не отговаря: {str(exc)[:100]}")]
    named = os.environ.get("JARVIS_ADB_DEVICES", "")
    if serials:
        return [finding(OK, "Телефон (adb)", f"свързан: {', '.join(serials)}")]
    if named:
        return [finding(WARN, "Телефон (adb)", "записан е, но сега не е свързан: включи Wi-Fi дебъг на телефона или кажи "
                                               "„свържи телефона“ (android_wireless)")]
    return [finding(WARN, "Телефон (adb)", "никой телефон не е свързан; кажи „свържи телефона“")]


def folders(settings) -> list[dict]:
    out = []
    home = Path(settings.home)
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(finding(OK, "Папка с данни", str(home)))
    except OSError as exc:
        out.append(finding(BAD, "Папка с данни", f"{home}: не мога да пиша в нея ({exc})"))
    found = browsers.find()
    chosen = os.environ.get("JARVIS_BROWSER", "").strip()
    if found:
        out.append(finding(OK, "Браузър за сайтовете", found[0] + (f" (настройка JARVIS_BROWSER={chosen})" if chosen else " (този по подразбиране)")))
    else:
        out.append(finding(WARN, "Браузър за сайтовете", "не намирам браузър; задай браузъра в Настройки > Поведение"
                           + (f" ({chosen} не е намерен)" if chosen else "")))
    vault = Path(settings.vault)
    out.append(finding(OK, "Бележки (vault)", str(vault)) if vault.exists()
               else finding(WARN, "Бележки (vault)", f"{vault} още я няма; появява се при първата бележка"))
    try:
        free = shutil.disk_usage(str(home if home.exists() else Path.home())).free / 1e9
        out.append(finding(OK if free > 5 else WARN if free > 1 else BAD, "Свободно място",
                           f"{free:.1f} GB" + ("" if free > 5 else "; disk_space показва какво заема мястото")))
    except OSError:
        pass
    db = home / "jarvis.db"
    if db.exists():
        size = db.stat().st_size / 1e6
        out.append(finding(OK if size < 500 else WARN, "База с разговорите", f"{size:.0f} MB"))
    return out


def jobs(store, now: datetime | None = None) -> list[dict]:
    """The scheduled jobs: how many, and ones that run often for nothing (a self-improvement loop Jarvis set
    himself keeps the laptop busy every half hour and answers "Готово." with nothing done)."""
    from .plugins.tasks import JOB
    from .routines import MARK

    now = now or datetime.now()
    rows = store.query("SELECT id, text, at, repeat FROM reminders WHERE fired=0 AND channels=? ORDER BY at", (JOB,))
    out = [finding(OK, "Задачи по график", f"{len(rows)}" if rows else "няма")]
    for r in rows:
        text = r["text"] or ""
        if MARK.match(text):
            continue  # a routine from Settings
        low = text.lower()
        if any(w in low for w in BUSY_JOB) and (r["repeat"] or "").strip():
            out.append(finding(WARN, f"Задача #{r['id']} „{text[:50]}“",
                               f"повтаря се ({r['repeat']}) и държи лаптопа зает, без да свърши нещо; "
                               f"изтрий я: „изтрий задачата {r['id']}“ или от Задачи по график"))
    return out


def failures(store, hours: int = 24) -> list[dict]:
    since = (datetime.now() - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    rows = store.query("SELECT tool, ok FROM activity WHERE created >= ?", (since,))
    if not rows:
        return [finding(OK, f"Стъпки за последните {hours} ч.", "няма")]
    failed: dict[str, int] = {}
    for r in rows:
        if not r["ok"]:
            failed[r["tool"]] = failed.get(r["tool"], 0) + 1
    if not failed:
        return [finding(OK, f"Стъпки за последните {hours} ч.", f"{len(rows)}, нито една неуспешна")]
    top = sorted(failed.items(), key=lambda kv: -kv[1])[:4]
    share = sum(failed.values()) / len(rows)
    return [finding(WARN if share < 0.3 else BAD, f"Стъпки за последните {hours} ч.",
                    f"{len(rows)}, неуспешни {sum(failed.values())}: " + ", ".join(f"{t} ×{n}" for t, n in top)
                    + ("; review_myself казва защо" if share >= 0.3 else ""))]


def version(root: Path | None = None) -> list[dict]:
    from . import updater

    root = root or updater.project_root()
    installed = updater.installed_version(root)
    return [finding(OK, "Версия", installed[:12] if installed else "неизвестна (инсталирана ръчно); „jarvis update“ взима последната")]


def examine(ctx) -> list[dict]:
    """Every check, each wrapped so one that crashes still leaves the rest."""
    out: list[dict] = []
    checks = [lambda: brains(ctx), packages, phone, lambda: folders(ctx.settings),
              lambda: jobs(ctx.store), lambda: failures(ctx.store), version]
    for check in checks:
        try:
            out.extend(check())
        except Exception as exc:
            log.warning("a doctor's check failed: %s", exc, exc_info=True)
            out.append(finding(WARN, "Една проверка не мина", f"{type(exc).__name__}: {str(exc)[:120]}"))
    return out


def report(findings: list[dict]) -> str:
    """The findings as lines for the owner, the problems first, then one line of verdict."""
    order = {BAD: 0, WARN: 1, OK: 2}
    lines = [f"{MARK[f['state']]} {f['what']}" + (f" — {f['hint']}" if f["hint"] else "")
             for f in sorted(findings, key=lambda f: order[f["state"]])]
    bad = sum(1 for f in findings if f["state"] == BAD)
    warn = sum(1 for f in findings if f["state"] == WARN)
    verdict = ("Всичко важно работи." if not bad and not warn else
               f"{bad} неща не работят, {warn} за оправяне." if bad else f"Работи; {warn} неща може да се оправят.")
    return "\n".join([verdict, *lines])
