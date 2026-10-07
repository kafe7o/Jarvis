"""The brain: a Claude tool-use loop over every registered plugin."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

import anthropic

from .config import Settings
from .store import Store
from .tools import Confirmer, ToolRegistry

log = logging.getLogger("jarvis.brain")

PERSONA = """You are J.A.R.V.I.S., the personal AI assistant of {user}. You run on their own computer \
and act for them in the real world through your tools: phone calls and SMS, e-mail, payments, tasks, \
reminders, calendar, web search, files, programs and the shell.

How you work:
- Answer in the language the user writes or speaks in; the default is {language_name}. \
Address the user as "{user}". Be concise, warm and dryly witty, like Jarvis from Iron Man.
- Replies may be read aloud: keep them short, no markdown tables unless asked.
- You can do practically anything a person at this computer can. When a request needs action, act \
rather than describe; chain as many tools as needed and finish the job. If no dedicated tool fits, \
use the browser (web_browser), see and operate any program (look_at_screen + control_input), \
run_python or run_shell, and for recurring needs teach yourself a new tool with create_skill.
- Before anything irreversible that you do through the browser, the screen or code (paying, ordering, \
posting, messaging people), call request_approval with the exact details.
- For work that should happen later or regularly on its own ("every morning check my mail"), use \
schedule_job.
- Actions that spend money or reach other people (calls, SMS, e-mail, payments, refunds) ask the \
user for confirmation automatically before they run; just call the tool with complete, exact details. \
If the user declines, accept it and do not retry.
- Look up phone numbers and e-mail addresses with the contacts tools before asking the user.
- Store lasting facts about the user (preferences, people, plans) with the remember tool, without \
being asked, and use what you already know.
- If a tool says a service is not configured, tell the user which keys to add to .env.
- Never invent results; if something fails, say what failed."""

LANGUAGE_NAMES = {"bg": "Bulgarian", "en": "English", "de": "German", "ru": "Russian"}

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]


class Jarvis:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        registry: ToolRegistry,
        confirmer: Confirmer,
        client: anthropic.Anthropic | None = None,
        on_progress: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.registry = registry
        self.confirmer = confirmer
        self.client = client or anthropic.Anthropic()
        self.on_progress = on_progress or (lambda _msg: None)

    def system_prompt(self) -> str:
        s = self.settings
        prompt = PERSONA.format(user=s.user_name, language_name=LANGUAGE_NAMES.get(s.language, s.language))
        facts = self.store.query("SELECT topic, fact FROM facts ORDER BY id")
        if facts:
            prompt += "\n\nWhat you remember about the user:\n" + "\n".join(f"- [{f['topic']}] {f['fact']}" for f in facts)
        now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
        prompt += f"\n\nCurrent local time: {now} ({s.timezone})."
        return prompt

    def _request(self, messages: list) -> object:
        s = self.settings
        kwargs = dict(
            model=s.model,
            max_tokens=s.max_tokens,
            system=self.system_prompt(),
            messages=messages,
            tools=self.registry.definitions() + SERVER_TOOLS,
            output_config={"effort": s.effort},
        )
        if s.refusal_fallback:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return self.client.beta.messages.create(**kwargs)

    def ask(self, text: str, conversation: str = "main", confirmer: Confirmer | None = None) -> str:
        """Handle one user message end to end and return Jarvis's final answer."""
        messages: list = []
        for row in self.store.history(conversation):
            if messages and messages[-1]["role"] == row["role"]:
                messages[-1]["content"] += "\n\n" + row["content"]
            else:
                messages.append({"role": row["role"], "content": row["content"]})
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": "user", "content": text})

        answer = self._loop(messages, confirmer or self.confirmer)
        self.store.add_message(conversation, "user", text)
        self.store.add_message(conversation, "assistant", answer)
        return answer

    def _loop(self, messages: list, confirmer: Confirmer) -> str:
        texts: list[str] = []
        for _ in range(self.settings.max_tool_rounds):
            response = self._request(messages)
            texts = [b.text for b in response.content if b.type == "text" and b.text.strip()] or texts

            if response.stop_reason == "refusal":
                return "Съжалявам, не мога да помогна с това."
            if response.stop_reason not in ("tool_use", "pause_turn"):
                break
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continue

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                self.on_progress(block.name)
                output, is_error = self.registry.run(
                    block.name, dict(block.input or {}), confirmer, self.settings.trust_local_actions
                )
                log.info("tool %s -> %s", block.name, output[:200] if isinstance(output, str) else "[image]")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        else:
            texts.append("(Спрях: твърде много стъпки за една заявка.)")
        return "\n\n".join(texts) if texts else "Готово."
