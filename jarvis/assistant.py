"""Ядрото на Jarvis: разговор с Claude, история и извикване на инструменти."""

import os

import anthropic

from .memory import MEMORY_TOOL_NAMES, MEMORY_TOOLS, MemoryStore
from .tools import TOOLS, run_tool

DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 4096

SYSTEM_PROMPT = (
    "Ти си Jarvis, личен AI асистент. Говориш учтиво, кратко и по същество, "
    "с лека доза британски хумор. Отговаряй на езика, на който ти пишат "
    "(по подразбиране на български). Използвай наличните инструменти, когато помагат."
)


class Jarvis:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        memory: MemoryStore | None = None,
    ):
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("JARVIS_MODEL", DEFAULT_MODEL)
        self.memory = memory or MemoryStore()
        self.history: list[dict] = []
        self._system = SYSTEM_PROMPT

    def reset(self) -> None:
        self.history.clear()

    def ask(self, text: str) -> str:
        """Изпраща съобщение, изпълнява инструментите при нужда и връща отговора."""
        start = len(self.history)
        self.history.append({"role": "user", "content": text})
        # Фактите от дълготрайната памет, свързани с това съобщение, отиват в system prompt-а.
        self._system = SYSTEM_PROMPT + self.memory.prompt_section(text)
        try:
            return self._run()
        except Exception:
            # При грешка връщаме историята както беше, за да не остане недовършен ход.
            del self.history[start:]
            raise

    def _run(self) -> str:
        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=self._system,
                tools=[*TOOLS, *MEMORY_TOOLS],
                messages=self.history,
            )
            self.history.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                return "".join(b.text for b in response.content if b.type == "text").strip()

            results = []
            for block in response.content:
                if block.type == "tool_use":
                    if block.name in MEMORY_TOOL_NAMES:
                        output = self.memory.handle_tool(block.name, block.input)
                        is_error = output.startswith(("Error", "No fact", "Unknown"))
                    else:
                        output, is_error = run_tool(block.name, block.input)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                            "is_error": is_error,
                        }
                    )
            self.history.append({"role": "user", "content": results})
