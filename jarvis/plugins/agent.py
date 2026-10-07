"""Open-ended abilities: approval for free-form actions, and skills Jarvis writes for itself.

A skill is a Python file in ~/.jarvis/skills/ with ``register(registry, ctx)``, exactly like a
built-in plugin. Jarvis can create one when you ask for something no tool covers yet
(a smart-home API, a bank's export, a game launcher...), and it is available from then on.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from pathlib import Path

from ..tools import ToolRegistry, obj

log = logging.getLogger("jarvis.skills")

SKILL_TEMPLATE_HINT = '''from jarvis.tools import obj

def register(registry, ctx):
    @registry.tool("What it does.", obj({"arg": ("string", "Meaning")}))
    def my_tool(arg: str):
        return "result"
'''


def skills_dir(ctx) -> Path:
    path = ctx.settings.home / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_skill(registry: ToolRegistry, ctx, path: Path) -> list[str]:
    before = set(registry.tools)
    spec = importlib.util.spec_from_file_location(f"jarvis_skill_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.register(registry, ctx)
    return sorted(set(registry.tools) - before)


def load_skills(registry: ToolRegistry, ctx) -> None:
    for path in sorted(skills_dir(ctx).glob("*.py")):
        try:
            load_skill(registry, ctx, path)
        except Exception:
            log.exception("skill %s failed to load", path.name)


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Ask the user to approve an action you are about to take through the browser, the screen or code "
        "(placing an order, paying on a website, posting, messaging someone, deleting accounts). "
        "Describe exactly what will happen, to whom, and for how much. Returns whether they approved.",
        obj({"action": ("string", "Exact description of what you will do")}),
        confirm=True,
        summarize=lambda a: a.get("action", ""),
    )
    def request_approval(action: str):
        return "Approved. Go ahead."

    @registry.tool(
        "Teach yourself a new ability: write a Python skill that registers new tools, and load it now. "
        "Use when the user asks for something no existing tool can do well. The file must define "
        "register(registry, ctx) and decorate tools with @registry.tool(description, schema, confirm=...). "
        "Set confirm=True for tools that spend money or contact people. ctx.settings and ctx.store "
        "(SQLite: ctx.store.query/execute) are available. Example:\n" + SKILL_TEMPLATE_HINT,
        obj({
            "name": ("string", "snake_case file name for the skill"),
            "code": ("string", "Full Python source"),
            "pip_packages?": ("array", "Packages to pip install first"),
        }),
        confirm=True,
        local=True,
        summarize=lambda a: f"Ново умение „{a.get('name')}“"
        + (f" (инсталира: {', '.join(a.get('pip_packages') or [])})" if a.get("pip_packages") else "")
        + f":\n{a.get('code')}",
    )
    def create_skill(name: str, code: str, pip_packages: list | None = None):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", name):
            raise ValueError("name must be snake_case letters/digits")
        if pip_packages:
            import subprocess
            import sys

            proc = subprocess.run([sys.executable, "-m", "pip", "install", *pip_packages], capture_output=True, text=True)
            if proc.returncode:
                return f"pip install failed:\n{proc.stderr[-3000:]}"
        path = skills_dir(ctx) / f"{name}.py"
        old = {t for t in registry.tools}
        path.write_text(code, encoding="utf-8")
        try:
            # Reloading a skill replaces its earlier tools.
            added = load_skill(registry, ctx, path)
        except Exception as exc:
            path.unlink()
            for tool in set(registry.tools) - old:
                registry.tools.pop(tool)
            return f"The skill failed to load and was not saved: {type(exc).__name__}: {exc}"
        return f"Skill saved to {path}. New tools available now: {', '.join(added) or '(updated existing tools)'}"

    @registry.tool("List the skills you have taught yourself.", obj({}))
    def list_skills():
        return [p.stem for p in sorted(skills_dir(ctx).glob("*.py"))]

    @registry.tool("Read the source of one of your skills.", obj({"name": ("string", "Skill name")}))
    def read_skill(name: str):
        return (skills_dir(ctx) / f"{name}.py").read_text(encoding="utf-8")

    @registry.tool(
        "Delete one of your skills (takes effect after restart).",
        obj({"name": ("string", "Skill name")}),
        confirm=True,
        local=True,
        summarize=lambda a: f"Изтрий умение „{a.get('name')}“",
    )
    def delete_skill(name: str):
        (skills_dir(ctx) / f"{name}.py").unlink()
        return "Deleted."

    load_skills(registry, ctx)
