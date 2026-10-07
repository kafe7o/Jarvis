"""Инструменти, които Jarvis може да извиква по време на разговор."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TOOLS = [
    {
        "name": "get_current_time",
        "description": (
            "Връща текущата дата и час. Използвай го, когато потребителят пита "
            "колко е часът, коя дата е или кой ден от седмицата е."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA часова зона, напр. 'Europe/Sofia'. По подразбиране: Europe/Sofia.",
                }
            },
        },
    },
    {
        "name": "web_search",
        "description": (
            "Търси в интернет по зададена заявка. Използвай го за актуална информация, "
            "която не знаеш."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Текстът за търсене."}
            },
            "required": ["query"],
        },
    },
]


def get_current_time(timezone: str = "Europe/Sofia") -> str:
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Непозната часова зона: {timezone}"
    now = datetime.now(tz)
    return now.strftime(f"%Y-%m-%d %H:%M:%S ({timezone}), %A")


def web_search(query: str) -> str:
    # Заглушка: истинско търсене ще бъде добавено по-късно.
    return (
        f"[web_search е заглушка] Търсенето за '{query}' още не е свързано с "
        "истинска търсачка. Кажи на потребителя, че не можеш да търсиш в интернет засега."
    )


_HANDLERS = {
    "get_current_time": get_current_time,
    "web_search": web_search,
}


def run_tool(name: str, tool_input: dict) -> tuple[str, bool]:
    """Изпълнява инструмент и връща (резултат, дали е грешка)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Непознат инструмент: {name}", True
    try:
        return handler(**tool_input), False
    except Exception as exc:  # грешката се връща на модела, вместо да спира разговора
        return f"Грешка в {name}: {exc}", True
