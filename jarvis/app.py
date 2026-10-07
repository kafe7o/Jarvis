"""Wires settings, storage, plugins and the brain together."""

from __future__ import annotations

from typing import Callable

from .brain import Jarvis
from .config import Settings, settings as default_settings
from .plugins import Context, load_all
from .store import Store
from .tools import Confirmer, ToolRegistry


def build(
    confirmer: Confirmer,
    notifiers: list[Callable[[str], None]] | None = None,
    settings: Settings | None = None,
    client=None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[Jarvis, Context]:
    settings = settings or default_settings
    store = Store(settings.db_path)
    ctx = Context(settings=settings, store=store, notifiers=list(notifiers or []))
    registry = ToolRegistry()
    load_all(registry, ctx)
    jarvis = Jarvis(settings, store, registry, confirmer, client=client, on_progress=on_progress)
    ctx.jarvis = jarvis
    return jarvis, ctx


def start_background(ctx: Context) -> None:
    """Start reminders and, when configured, the phone webhook server."""
    ctx.scheduler.start()
    import os

    if os.environ.get("JARVIS_PUBLIC_URL"):
        from .phone_agent import ensure_server

        ensure_server(ctx)
    if os.environ.get("JARVIS_WEB_TOKEN") and not getattr(ctx, "hub", None):
        from .hub import Hub

        Hub(ctx, os.environ["JARVIS_WEB_TOKEN"], int(os.environ.get("JARVIS_WEB_PORT", "8770"))).serve()
