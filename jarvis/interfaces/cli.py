"""Text chat in the terminal."""

from __future__ import annotations

import sys

from ..app import build, start_background

TOOL_LABELS = {"web_search": "търся в интернет", "web_fetch": "чета страница"}


def console_confirm(summary: str) -> bool:
    print(f"\n⚠  Jarvis иска да направи:\n   {summary}")
    try:
        answer = input("   Потвърждаваш ли? [да/не]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"да", "д", "y", "yes", "ok", "ок"}


def notify(text: str) -> None:
    print(f"\n🔔 {text}\n> ", end="", flush=True)


def progress(tool: str) -> None:
    print(f"   … {TOOL_LABELS.get(tool, tool)}", flush=True)


def chat() -> None:
    jarvis, ctx = build(console_confirm, [notify], on_progress=progress)
    start_background(ctx)
    print(f"J.A.R.V.I.S. е на линия, {jarvis.settings.user_name}. (/нов – нов разговор, /изход – край)")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in {"/изход", "/exit", "/quit"}:
            break
        if text in {"/нов", "/new"}:
            ctx.store.clear_history("main")
            print("Започваме на чисто.")
            continue
        try:
            print(f"\nJarvis: {jarvis.ask(text)}\n")
        except Exception as exc:
            print(f"\n[грешка] {type(exc).__name__}: {exc}\n", file=sys.stderr)


def ask_once(text: str) -> None:
    jarvis, _ctx = build(console_confirm, [print], on_progress=progress)
    print(jarvis.ask(text, conversation="oneshot"))
