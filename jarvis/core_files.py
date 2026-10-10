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
    "LESSONS.md": ("Какво е научил Jarvis", "Уроци, които Jarvis сам си записва от опит: грешки, твоите поправки, "
                   "какво е проработило. Следва ги във всяка задача. Можеш да ги поправяш или триеш."),
}
MAX_LESSONS = 40  # the newest are kept: they go with every request


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


def norm(text: str) -> str:
    return " ".join(text.lower().replace("-", " ").split()).strip(" .")


def learn(home: Path, lesson: str) -> bool:
    """Add a lesson to LESSONS.md (one line, the newest last); False when it is empty or already there."""
    lesson = " ".join(str(lesson or "").split()).strip(" -")[:300]
    if len(lesson) < 8:
        return False
    lines = [line for line in read(home, "LESSONS.md").splitlines() if line.strip()]
    known = [norm(line) for line in lines]
    if any(norm(lesson) in k or k.endswith(norm(lesson)) for k in known):
        return False
    lessons = [line for line in lines if line.startswith("- ")]
    others = [line for line in lines if not line.startswith("- ")]
    write(home, "LESSONS.md", "\n".join(others + (lessons + [f"- {lesson}"])[-MAX_LESSONS:]))
    return True

