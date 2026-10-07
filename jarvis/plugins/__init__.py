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
           "payments", "sendmoney", "agent"]  # agent last: loads skills


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


def load_all(registry: ToolRegistry, ctx: Context, names: list[str] | None = None) -> None:
    for name in names or BUILTIN:
        module = importlib.import_module(f"{__name__}.{name}")
        module.register(registry, ctx)


class NotConfigured(RuntimeError):
    """Raised when a plugin needs credentials the user has not set yet."""

    def __init__(self, service: str, env_vars: list[str]):
        super().__init__(f"{service} is not configured. Add to .env: {', '.join(env_vars)}")
