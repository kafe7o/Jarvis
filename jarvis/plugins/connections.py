"""What is connected and what is not, and saving a key Jarvis obtained: the Connections tab, as tools.

Without these the brain could only tell the owner which keys to type. Now it sees which services are set up
(``connections``), opens the page where a key is made (the browser or ``open``), and once it has the key,
from the page or from the owner, stores it itself (``save_setting``), as the Settings tab would.
"""

from __future__ import annotations

import re

from ..tools import ToolRegistry, obj
from . import save_settings

# How a key is used once saved: most services are read when Jarvis starts.
RESTART = "It works after the next restart (say \"restart\" or use update_jarvis), except JARVIS_* settings, which apply now."


def known_fields() -> list[tuple[str, str, list[tuple[str, str]]]]:
    from ..hub import EXTRA_SETTINGS
    from ..setup_wizard import STEPS

    return STEPS + EXTRA_SETTINGS


def page_of(where: str) -> str:
    """The address in a "where to get it" note: "console.groq.com/keys > Create API Key" -> https://console.groq.com/keys."""
    first = re.split(r"\s*(?:>|/\s|\(| и )", where or "", maxsplit=1)[0].strip()
    if "." in first and " " not in first and not first.startswith("@"):
        return first if first.startswith("http") else "https://" + first
    return ""


def register(registry: ToolRegistry, ctx) -> None:
    def owner_only() -> None:
        from ..brain import TURN

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner sees and changes the connections.")

    def stored() -> dict[str, str]:
        import os

        from ..setup_wizard import env_path, read_env

        values = read_env(env_path())
        return {k: values.get(k) or os.environ.get(k, "") for _t, _w, fields in known_fields() for k, _l in fields}

    @registry.tool(
        "Which services are connected and which keys are missing (the Settings > Връзки tab): for every service the "
        "keys, whether each is set, and the page where a key is made. Call it before saying that something is not "
        "set up, and before helping to set it up: open the page in the browser, and when the key is on the screen "
        "save it with save_setting.",
        obj({"service?": ("string", "Only the services whose name contains this (e.g. 'Telegram')")}),
    )
    def connections(service: str = ""):
        owner_only()
        values, lines = stored(), []
        for title, where, fields in known_fields():
            if service and service.casefold() not in title.casefold():
                continue
            ready = all(values.get(k) for k, _l in fields)
            lines.append(f"{'✅' if ready else '❌'} {title}" + (f" — {where}" if where else "")
                         + (f" ({page_of(where)})" if page_of(where) else ""))
            for key, label in fields:
                lines.append(f"    {'set' if values.get(key) else 'MISSING'}: {key} — {label}")
        return "\n".join(lines) or f"No service is called '{service}'."

    @registry.tool(
        "Save one setting or key into Jarvis's .env, as the Settings > Връзки tab does: a key you read from the page "
        "where it was made, or one the owner gave you. Only keys the Connections tab knows. Never guess or invent a "
        "key: save only a value you have actually seen.",
        obj({"key": ("string", "The setting, e.g. TELEGRAM_BOT_TOKEN"), "value": ("string", "Its value; empty removes it")}),
        confirm=True, local=True,
        summarize=lambda a: f"Запис в настройките: {a.get('key', '?')} = {'(празно)' if not a.get('value') else '••••' if 'KEY' in str(a.get('key')) or 'TOKEN' in str(a.get('key')) or 'SECRET' in str(a.get('key')) or 'PASSWORD' in str(a.get('key')) else a.get('value')}",
    )
    def save_setting(key: str, value: str = ""):
        owner_only()
        key = (key or "").strip().upper()
        known = {k: label for _t, _w, fields in known_fields() for k, label in fields}
        if key not in known:
            close = [k for k in known if key and key.split("_")[0] in k][:6]
            return f"No setting is called {key}." + (f" Did you mean: {', '.join(close)}?" if close else "")
        value = (value or "").strip()
        if "\n" in value:
            return "A setting is one line."
        save_settings(ctx, {key: value})
        if not value:
            return f"Removed {key}."
        return f"Saved {key} ({known[key]}). " + ("It applies now." if key.startswith("JARVIS_") else RESTART)
