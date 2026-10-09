"""Understanding spoken or typed yes/no answers to confirmation questions."""

from __future__ import annotations

import re


def is_yes(text: str) -> bool:
    words = re.findall(r"\w+", text.lower())
    if any(w in {"не", "no", "недей", "откажи", "отказ", "стоп", "cancel"} for w in words):
        return False
    return any(w in {"да", "yes", "yeah", "потвърждавам", "давай", "действай", "ок", "окей", "ok", "добре", "разбира"} for w in words)

