"""Конзолен чат с Jarvis. Стартира се с: python -m jarvis"""

import os
import sys

import anthropic

from .assistant import Jarvis

HELP = "Команди: /reset изчиства историята, /memory показва паметта, /exit (или Ctrl+D) излиза."


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Липсва ANTHROPIC_API_KEY. Задайте я, напр.: export ANTHROPIC_API_KEY=sk-ant-...")
        return 1

    jarvis = Jarvis()
    print(f"Jarvis е на линия (модел: {jarvis.model}). {HELP}")
    while True:
        try:
            text = input("\nВие: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nДовиждане, сър.")
            return 0
        if not text:
            continue
        if text in ("/exit", "/quit"):
            print("Довиждане, сър.")
            return 0
        if text == "/reset":
            jarvis.reset()
            print("Историята е изчистена.")
            continue
        if text == "/memory":
            facts = jarvis.memory.all()
            print("\n".join(f.as_line() for f in facts) if facts else "Паметта е празна.")
            continue
        try:
            print(f"\nJarvis: {jarvis.ask(text)}")
        except anthropic.APIError as exc:
            print(f"\n[Грешка от API: {exc}]")


if __name__ == "__main__":
    sys.exit(main())
