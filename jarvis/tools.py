"""Tool registry: plugins register Python functions that the brain can call.

A tool marked ``confirm=True`` never runs until the user approves that exact
call (who, what, how much). Tools marked ``local=True`` can skip the prompt when
JARVIS_TRUST_LOCAL is on; calls, messages and payments can never skip it.
"""

from __future__ import annotations

import contextvars
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class Image:
    """Return this from a tool to let the brain see a picture (e.g. a screenshot)."""

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


# Set while a confirmation is being asked: which tool, and whether "always allow" may be offered for it
# (only computer actions that JARVIS_TRUST_LOCAL covers; never calls, messages or payments).
asking: contextvars.ContextVar[dict | None] = contextvars.ContextVar("asking", default=None)


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

    def __post_init__(self) -> None:
        # Every schema is checked here, whoever wrote it (a plugin, a skill Jarvis wrote for himself, another
        # device): one "int" instead of "integer" makes Gemini refuse the whole request, every tool included.
        self.input_schema = normalize_schema(self.input_schema)

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
        if needs_ok:
            mark = asking.set({"tool": name, "can_always": tool.local and group not in (ask_groups or ())})
            try:
                ok = confirmer(tool.describe_call(args))
            finally:
                asking.reset(mark)
            if not ok:
                return "The user declined this action. Do not retry it unless they ask again.", True
        args, problem = fit_args(tool.input_schema, args)
        if problem:  # said plainly, so the model fixes the call instead of guessing from a Python traceback
            return problem, True
        try:
            result = tool.func(**args)
        except TypeError as exc:
            if "argument" in str(exc) and "()" in str(exc):  # a wrong set of arguments, not a failure inside
                return f"Wrong arguments for {name}: {exc}. The tool takes: {describe_schema(tool.input_schema)}", True
            return f"{type(exc).__name__}: {exc}", True
        except Exception as exc:  # report every failure back to the model
            return f"{type(exc).__name__}: {exc}", True
        if isinstance(result, str):
            return result, False
        if isinstance(result, Image):
            return result.content(), False
        return json.dumps(result, ensure_ascii=False, default=str), False


_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}

# JSON Schema's type names, and the names people (and models writing skills) use instead of them. Gemini
# rejects anything else with "unrecognized type 'int'", and does so for the whole request.
JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}
TYPE_ALIASES = {"int": "integer", "integer": "integer", "long": "integer", "str": "string", "string": "string",
                "text": "string", "bool": "boolean", "boolean": "boolean", "float": "number", "double": "number",
                "number": "number", "decimal": "number", "dict": "object", "object": "object", "map": "object",
                "list": "array", "array": "array", "tuple": "array", "set": "array", "null": "null", "none": "null"}
# Keys Gemini's function declarations do not accept, dropped before sending.
UNSUPPORTED_KEYS = {"$schema", "$id", "additionalProperties", "default", "examples", "title"}


def json_type(name) -> str:
    """The JSON Schema type for ``name`` ("int" -> "integer"); "string" for anything unknown ("any", "file")."""
    return TYPE_ALIASES.get(str(name).strip().lower(), "string")


def normalize_schema(schema) -> dict:
    """``schema`` made acceptable to every brain: a JSON Schema object with only standard type names, arrays
    with items and no keys the APIs reject. A missing or broken schema becomes an object with no properties."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": []}
    out = _normalize(dict(schema))
    if out.get("type") != "object":
        out["type"] = "object"
    out.setdefault("properties", {})
    out["required"] = [r for r in out.get("required", []) if r in out["properties"]]
    return out


def _normalize(node):
    if isinstance(node, list):
        return [_normalize(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYS:
            continue
        if key == "type":
            if isinstance(value, list):  # ["string", "null"] -> the first real type
                value = next((v for v in value if json_type(v) != "null"), "string")
            value = json_type(value)
        elif key == "properties" and isinstance(value, dict):
            value = {str(name): _normalize(prop if isinstance(prop, dict) else {"type": prop}) for name, prop in value.items()}
        elif key in ("items", "not") or key.endswith("Of") or key == "if":
            value = _normalize(value)
        elif key in ("required", "enum", "description"):
            pass
        else:
            value = _normalize(value)
        out[key] = value
    if out.get("type") == "array" and not isinstance(out.get("items"), dict):
        out["items"] = {"type": "string"}
    if out.get("type") == "object" and "properties" in out and not isinstance(out["properties"], dict):
        out["properties"] = {}
    return out


def describe_schema(schema: dict) -> str:
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    return ", ".join(f"{k}: {v.get('type', 'string')}{'' if k in required else '?'}" for k, v in props.items()) or "(no arguments)"


_FALSE = {"false", "no", "0", "не", "off", ""}


def fit_args(schema: dict, args: dict) -> tuple[dict, str | None]:
    """The model's arguments made to fit the tool: "5" becomes 5 for an integer, "true" True for a boolean, a
    JSON string for an array or object is parsed. Returns the arguments and a message when the call cannot be
    made (missing or unknown arguments), for the model to correct it."""
    props = (schema or {}).get("properties") or {}
    if not isinstance(args, dict):
        return {}, f"Arguments must be an object with: {describe_schema(schema)}"
    if not props:
        return {} if args else args, None
    fitted: dict = {}
    for key, value in args.items():
        prop = props.get(key)
        if prop is None:
            close = [k for k in props if k.lower() == str(key).lower()]  # "Port" for "port"
            if not close:
                return {}, f"Unknown argument '{key}'. The tool takes: {describe_schema(schema)}"
            key, prop = close[0], props[close[0]]
        fitted[key] = coerce(prop.get("type", "string"), value)
    missing = [r for r in schema.get("required", []) if fitted.get(r) is None]
    if missing:
        return {}, f"Missing required argument(s): {', '.join(missing)}. The tool takes: {describe_schema(schema)}"
    return fitted, None


def coerce(typ: str, value):
    if value is None or isinstance(value, bool) and typ != "integer":
        return value if typ != "string" or value is None else str(value).lower()
    try:
        if typ == "integer" and not isinstance(value, int):
            return int(float(str(value).strip())) if str(value).strip() else value
        if typ == "number" and not isinstance(value, (int, float)):
            return float(str(value).strip())
        if typ == "boolean" and not isinstance(value, bool):
            return str(value).strip().lower() not in _FALSE
        if typ in ("array", "object") and isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, (list, dict)):
                return parsed
            return [value] if typ == "array" else value
        if typ == "array" and not isinstance(value, list):
            return [value]
        if typ == "string" and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    except (ValueError, TypeError, json.JSONDecodeError):
        return value
    return value


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
