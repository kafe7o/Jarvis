"""Tool registry: plugins register Python functions that Claude can call.

A tool marked ``confirm=True`` never runs until the user approves that exact
call (who, what, how much). Tools marked ``local=True`` can skip the prompt when
JARVIS_TRUST_LOCAL is on; calls, messages and payments can never skip it.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class Image:
    """Return this from a tool to let Claude see a picture (e.g. a screenshot)."""

    data: bytes
    media_type: str = "image/png"
    text: str = ""

    def content(self) -> list[dict]:
        import base64

        blocks: list[dict] = [{
            "type": "image",
            "source": {"type": "base64", "media_type": self.media_type, "data": base64.b64encode(self.data).decode()},
        }]
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        return blocks


class Confirmer(Protocol):
    def __call__(self, summary: str) -> bool: ...


@dataclass
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    input_schema: dict
    confirm: bool = False
    local: bool = False
    summarize: Callable[[dict], str] | None = None
    group: str = ""  # permission group (the plugin it came from); see accounts.GROUPS

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def describe_call(self, args: dict) -> str:
        if self.summarize:
            return self.summarize(args)
        return f"{self.name}({json.dumps(args, ensure_ascii=False)})"


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def tool(
        self,
        description: str,
        schema: dict | None = None,
        *,
        name: str | None = None,
        confirm: bool = False,
        local: bool = False,
        summarize: Callable[[dict], str] | None = None,
    ):
        """Decorator: register ``func`` as a tool. Schema defaults to one built from the signature."""

        def wrap(func):
            self.add(
                Tool(
                    name=name or func.__name__,
                    description=description,
                    func=func,
                    input_schema=schema or schema_from_signature(func),
                    confirm=confirm,
                    local=local,
                    summarize=summarize,
                )
            )
            return func

        return wrap

    def definitions(self, allowed: set[str] | None = None) -> list[dict]:
        """Tool definitions; with ``allowed``, only tools whose group is in it."""
        return [t.definition() for t in self.tools.values() if allowed is None or (t.group or "agent") in allowed]

    def run(
        self,
        name: str,
        args: dict,
        confirmer: Confirmer,
        trust_local: bool = False,
        allowed: set[str] | None = None,
        ask_groups: set[str] | None = None,
    ) -> tuple[str | list, bool]:
        """Run a tool. Returns (result, is_error); result is text, or content blocks for images.

        ``allowed`` limits which permission groups may run at all; tools in ``ask_groups`` always
        ask first, even ones that normally don't.
        """
        tool = self.tools.get(name)
        if tool is None:
            return f"Unknown tool: {name}", True
        group = tool.group or "agent"
        if allowed is not None and group not in allowed:
            return "This account is not allowed to use this ability. Tell the user the owner can enable it in Settings > Permissions.", True
        needs_ok = (tool.confirm and not (tool.local and trust_local)) or group in (ask_groups or ())
        if needs_ok and not confirmer(tool.describe_call(args)):
            return "The user declined this action. Do not retry it unless they ask again.", True
        try:
            result = tool.func(**args)
        except Exception as exc:  # report every failure back to the model
            return f"{type(exc).__name__}: {exc}", True
        if isinstance(result, str):
            return result, False
        if isinstance(result, Image):
            return result.content(), False
        return json.dumps(result, ensure_ascii=False, default=str), False


_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def schema_from_signature(func: Callable) -> dict:
    props: dict[str, dict] = {}
    required: list[str] = []
    hints = inspect.get_annotations(func, eval_str=True)
    for pname, param in inspect.signature(func).parameters.items():
        hint = hints.get(pname, str)
        origin = getattr(hint, "__origin__", None)
        base = origin or hint
        if type(None) in getattr(hint, "__args__", ()):  # Optional[X] / X | None
            base = next(a for a in hint.__args__ if a is not type(None))
            base = getattr(base, "__origin__", None) or base
        props[pname] = {"type": _PY_TO_JSON.get(base, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    return {"type": "object", "properties": props, "required": required}


registry = ToolRegistry()


def obj(fields: dict[str, tuple[str, str]]) -> dict:
    """Compact schema builder: {"name": ("string", "desc"), "opt?": ("integer", "desc")}.

    A trailing ``?`` marks an optional field; ``"array"`` fields hold strings.
    """
    props: dict[str, dict] = {}
    required: list[str] = []
    for key, (typ, desc) in fields.items():
        optional = key.endswith("?")
        key = key.rstrip("?")
        prop: dict[str, Any] = {"type": typ, "description": desc}
        if typ == "array":
            prop["items"] = {"type": "string"}
        props[key] = prop
        if not optional:
            required.append(key)
    return {"type": "object", "properties": props, "required": required}
