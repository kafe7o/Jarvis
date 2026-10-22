"""What a brain answers, in the shape the tool loop in brain.py reads (the Claude Messages format it was first
written for): blocks of text and tool calls, why it stopped, and the tokens it took. Also the helpers that read
those messages, and the parser for a tool call a model wrote out as text instead of making it."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field


@dataclass
class Block:
    """A content block shaped like the Claude SDK's text and tool_use blocks."""

    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


class Reply(list):
    """The blocks of one answer, plus the model that wrote it."""

    model = None


@dataclass
class Answer:
    content: Reply
    stop_reason: str
    model: str | None = None
    usage: dict | None = None  # tokens, for the app's "Разход" tab (see usage.py)


CALL_ARGS = ("arguments", "parameters", "input", "args")
FENCE = re.compile(r"^```[\w-]*\s*|\s*```$|^<tool_call>\s*|\s*</tool_call>$|^\[TOOL_CALLS\]\s*", re.I)


# Qwen 3.5's own way of writing a call (Ollama sometimes passes it on as text instead of making it):
# <tool_call><function=run_shell><parameter=command>taskmgr</parameter></function></tool_call>
XML_CALL = re.compile(r"<function=([\w.-]+)>(.*?)</function>", re.S)
XML_PARAM = re.compile(r"<parameter=([\w.-]+)>\n?(.*?)\n?</parameter>", re.S)
TAGGED = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
JSON_VALUE = re.compile(r"[\[{].*|true|false|null|-?(0|[1-9]\d*)(\.\d+)?", re.S)


def xml_call(text: str) -> tuple[str, dict] | None:
    found = XML_CALL.search(text or "")
    if not found:
        return None
    args = {}
    for key, value in XML_PARAM.findall(found[2]):
        try:
            args[key] = json.loads(value) if JSON_VALUE.fullmatch(value.strip()) else value
        except ValueError:
            args[key] = value
    return found[1], args


def written_call(text: str) -> tuple[str, dict] | None:
    """A tool call the model wrote out as its answer instead of making it, e.g. {"name": "run_shell", "arguments":
    {"command": "taskmgr"}} (a weak model does that; the owner once saw it printed in the chat), with or without a
    ```json fence or <tool_call> tags: (name, arguments). None when the text is an answer."""
    found = xml_call(text)
    if found:
        return found
    tagged = TAGGED.search(text or "")
    if tagged and tagged.group(0) != (text or "").strip():  # a call tagged after a sentence: the call
        return written_call(tagged[1])
    body = FENCE.sub("", FENCE.sub("", (text or "").strip())).strip()
    if not body.startswith(("{", "[")):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("function"), dict):  # {"type": "function", "function": {"name": .., "arguments": ..}}
        data = data["function"]
    name = data.get("name") or data.get("tool")
    if not any(k in data for k in CALL_ARGS):  # {"name": "Иван", "age": 3} is data, not a call
        return None
    args = next(data[k] for k in CALL_ARGS if k in data)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if not isinstance(name, str) or not re.fullmatch(r"[\w.-]+", name) or not isinstance(args, dict):
        return None
    return name, args


def _get(block, key, default=None):
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(_get(b, "text", "")) for b in content or [] if _get(b, "type") == "text")


def _images_of(content) -> list[tuple[str, bytes]]:
    if isinstance(content, str):
        return []
    out = []
    for b in content or []:
        source = _get(b, "source") or {}
        if _get(b, "type") == "image" and _get(source, "type") == "base64":
            out.append((_get(source, "media_type"), base64.b64decode(_get(source, "data"))))
    return out


KEEP_FULL = 4  # the latest rounds of tool results go in full
OLD_RESULT = 1500  # characters kept of an older tool result; its pictures are left out


def shorten(result: str, pictures: int) -> str:
    """An older tool result, cut to save room: every step sends the whole task again, and a laptop model has a
    small context, so old pages and windows in full would push the task itself out of it."""
    if len(result) > OLD_RESULT:
        result = result[:OLD_RESULT] + f" … ({len(result) - OLD_RESULT} more characters left out to save room)"
    return result + (" (its picture is left out to save room: look again if it is needed)" if pictures else "")
