"""The owner's "core files": plain text Jarvis reads before every answer.

SOUL.md is Jarvis's personality and standing orders, USER.md is what Jarvis should know about the
owner. Both live in ~/.jarvis/core, are edited in the app (Settings > Personality) or any editor,
and are kept short because they go with every request.
"""

from __future__ import annotations

from pathlib import Path

LIMIT = 8000
FILES = {
    "SOUL.md": ("Личността на Jarvis", "Как да говори, какво винаги да прави и какво никога.\n\n"
                "Например:\n- Говори като Jarvis от „Железния човек“: учтив, спокоен, с леко британско чувство за хумор.\n"
                "- Отговаря кратко. Първо прави, после докладва.\n- Сутрин ми казва най-важното за деня."),
    "USER.md": ("За мен", "Каквото Jarvis трябва да знае за теб.\n\nНапример:\n- Казвам се Анастас, живея в София.\n"
                "- Телефонът ми е Xiaomi, лаптопът е с Windows.\n- Важни хора: ..."),
}


def path(home: Path, name: str) -> Path:
    if name not in FILES:
        raise KeyError(name)
    return Path(home) / "core" / name


def read(home: Path, name: str) -> str:
    try:
        return path(home, name).read_text(encoding="utf-8").strip()[:LIMIT]
    except OSError:
        return ""


def write(home: Path, name: str, text: str) -> None:
    target = path(home, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text((text or "").strip()[:LIMIT] + "\n", encoding="utf-8")
