"""Plugins add capabilities to Jarvis. Each module exposes ``register(registry, ctx)``."""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Callable

from ..config import Settings
from ..store import Store
from ..tools import ToolRegistry

log = logging.getLogger("jarvis.plugins")

BUILTIN = ["memory", "tasks", "system", "browser", "android", "home", "comms", "messaging", "google",
           "payments", "sendmoney", "team", "brain_switch", "websearch", "daily", "agent"]  # agent last: loads skills


@dataclass
class Context:
    settings: Settings
    store: Store
    notifiers: list[Callable[[str], None]] = field(default_factory=list)

    def notify(self, text: str) -> None:
        for notifier in self.notifiers:
            try:
                notifier(text)
            except Exception:  # one broken channel must not silence the others
                log.exception("notifier failed")


# Tools that belong to a different permission group than the plugin that defines them.
# "core" tools are always available: they only plan and ask the user for approval.
GROUP_OVERRIDES = {"request_approval": "core", "make_plan": "core", "update_plan_step": "core",
                   "show_plan": "core", "search_history": "memory", "switch_brain": "core",
                   "google_search": "web", "weather": "web", "play_youtube": "system", "watch_video": "system"}


def load_all(registry: ToolRegistry, ctx: Context, names: list[str] | None = None) -> None:
    for name in names or BUILTIN:
        before = set(registry.tools)
        module = importlib.import_module(f"{__name__}.{name}")
        module.register(registry, ctx)
        for tool_name in set(registry.tools) - before:
            registry.tools[tool_name].group = GROUP_OVERRIDES.get(tool_name, name)


def save_settings(ctx: Context, values: dict[str, str]) -> None:
    """Remember settings in .env and apply them now (through the app when it runs)."""
    import os

    hub = getattr(ctx, "hub", None)
    if hub is not None:
        hub.save_settings(values)
        return
    from ..setup_wizard import env_path, read_env, write_env

    path = env_path()
    write_env(path, {**read_env(path), **values})
    os.environ.update({k: v for k, v in values.items() if v})


class NotConfigured(RuntimeError):
    """Raised when a plugin needs credentials the user has not set yet."""

    def __init__(self, service: str, env_vars: list[str]):
        super().__init__(f"{service} is not configured. Add to .env: {', '.join(env_vars)}")
