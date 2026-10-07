"""Свързване на гласа с чат ядрото.

По подразбиране гласът говори с ``jarvis.assistant.Jarvis``. С ``--brain
module:attr`` (или JARVIS_BRAIN) може да се ползва друго ядро: функция
``f(text) -> str``, обект с метод ``ask``/``chat``/``respond`` или клас,
който се създава без аргументи.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

Responder = Callable[[str], str]

_METHODS = ("ask", "chat", "respond")


def as_responder(obj: Any) -> Responder:
    if isinstance(obj, type):
        obj = obj()
    for name in _METHODS:
        method = getattr(obj, name, None)
        if callable(method):
            return method
    if callable(obj):
        return obj
    raise TypeError(f"{obj!r} не може да отговаря: нужна е функция или метод ask/chat/respond")


def load_responder(spec: str | None = None) -> Responder:
    if not spec:
        from jarvis.assistant import Jarvis

        return Jarvis().ask
    module_name, _, attr = spec.partition(":")
    obj = importlib.import_module(module_name)
    for part in filter(None, attr.split(".")):
        obj = getattr(obj, part)
    return as_responder(obj)
