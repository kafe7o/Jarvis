"""The brain: a tool-use loop over every registered plugin, thinking with Claude or Google Gemini."""

from __future__ import annotations

import contextvars
import logging
from datetime import datetime
from typing import Callable

import anthropic

from . import core_files, gemini, usage
from .config import Settings
from .store import Store
from .tools import Confirmer, ToolRegistry

log = logging.getLogger("jarvis.brain")

# The request being answered (user, confirmer, progress callback), so a tool such as delegate can
# start specialist agents that ask the same person and report to the same window.
TURN: contextvars.ContextVar[dict | None] = contextvars.ContextVar("jarvis_turn", default=None)

PERSONA = """You are J.A.R.V.I.S., the personal AI assistant of {user}. You run on their own computer \
and act for them in the real world through your tools: phone calls and SMS, e-mail, payments, tasks, \
reminders, calendar, web search, files, programs and the shell.

How you work:
- {language_rule} Address the user as "{user}". Be concise, warm and dryly witty, like Jarvis \
from Iron Man.
- Replies may be read aloud: keep them short, no markdown tables unless asked.
- You can do practically anything a person at this computer can. When a request needs action, act \
rather than describe; chain as many tools as needed and finish the job. If no dedicated tool fits, \
use the browser (web_browser), see and operate any program (look_at_screen + control_input), \
run_python or run_shell, and for recurring needs teach yourself a new tool with create_skill.
- You can act on the owner's other devices: tools named <device>__<tool> run on another computer, and \
the android tools control their phone (calls and SMS from their own number) and TV.
- Before anything irreversible that you do through the browser, the screen or code (paying, ordering, \
posting, messaging people), call request_approval with the exact details.
- Ask the user as little as possible: one confirmation per action. For calls and SMS from the phone use \
phone_call and phone_sms (device "phone"), which ask by themselves; never place them with adb shell \
commands, and never add request_approval on top of a tool that already asks. Don't ask in chat for \
permission to do what the user just asked; do it.
- You manage a team of specialists (delegate): for bigger jobs with parts that can run in parallel \
(research, content, inbox, planning, code), hand the parts to them and combine their reports.
- For goals that take several steps, first call make_plan to build a task tree, then work through it \
and keep it updated with update_plan_step; adapt the plan when something fails instead of giving up.
- Think ahead like a real assistant: notice what the user will need next, point out problems, and offer \
the obvious next step. When the user refers to something from the past, use search_history and recall.
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


def language_rule(language: str) -> str:
    if language == "bg":
        # The owner often types Bulgarian in Latin letters ("zashto raboti bavno"); answers are read aloud
        # by a Bulgarian voice, which can only read Cyrillic properly.
        return ("Always answer in Bulgarian written in Cyrillic, even when the user types Bulgarian in Latin "
                "letters (\"zashto raboti bavno\" is Bulgarian) and when tools, files or web pages are in another "
                "language: translate what you report. Use another language only when the user asks for it "
                "(a translation, a message to a foreigner).")
    return f"Answer in the language the user writes or speaks in; the default is {LANGUAGE_NAMES.get(language, language)}."

SERVER_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]


def claude_unusable(exc: Exception) -> bool:
    """True when Claude cannot answer at all: no credit left, or no or a wrong API key."""
    text = str(exc).lower()
    return (isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError))
            or "credit balance" in text or "authentication" in text or "api_key" in text)


def strip_thinking(messages: list) -> bool:
    """Remove thinking blocks from assistant turns in place. Returns True if any were removed."""
    removed = False
    for message in messages:
        if message["role"] != "assistant" or isinstance(message["content"], str):
            continue
        kept = [b for b in message["content"]
                if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) not in ("thinking", "redacted_thinking")]
        if len(kept) != len(message["content"]):
            message["content"] = kept
            removed = True
    return removed


class Jarvis:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        registry: ToolRegistry,
        confirmer: Confirmer,
        client: anthropic.Anthropic | None = None,
        on_progress: Callable[[str], None] | None = None,
        gemini_brain: gemini.GeminiBrain | None = None,
    ):
        self.settings = settings
        self.store = store
        self.registry = registry
        self.confirmer = confirmer
        self.client = client or anthropic.Anthropic()
        self.gemini = gemini_brain or gemini.GeminiBrain()
        self.on_progress = on_progress or (lambda _msg: None)

    def system_prompt(self, user=None) -> str:
        return self._base_prompt(user) + self._clock()

    def _clock(self) -> str:
        now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
        return f"\n\nCurrent local time: {now} ({self.settings.timezone})."

    def _base_prompt(self, user=None) -> str:
        """Everything in the system prompt except the clock, which changes every minute."""
        s = self.settings
        guest = user is not None and not user.is_owner
        prompt = PERSONA.format(user=user.name if guest else s.user_name, language_rule=language_rule(s.language))
        if guest:
            prompt += (f"\n\nYou are talking with {user.name}, who has their own account on this Jarvis. They are "
                       "not the owner: do not reveal the owner's private information (memories, contacts, mail, "
                       "files, messages) unless a tool you are allowed to use returns it for this request. Some "
                       "abilities are switched off for this account; if one is needed, say the owner can enable "
                       "it in Settings > Permissions.")
        soul = core_files.read(s.home, "SOUL.md")
        if soul:
            prompt += "\n\nYour personality and standing orders, written by the owner (SOUL.md):\n" + soul
        about = "" if guest else core_files.read(s.home, "USER.md")
        if about:
            prompt += "\n\nAbout the owner, in their own words (USER.md):\n" + about
        facts = [] if guest else self.store.query("SELECT topic, fact FROM facts ORDER BY id")
        if facts:
            prompt += "\n\nWhat you remember about the user:\n" + "\n".join(f"- [{f['topic']}] {f['fact']}" for f in facts)
        return prompt

    def _request(self, messages: list, user=None, system: str | list | None = None, allowed: set | None = None) -> object:
        s = self.settings
        if allowed is None and user is not None:
            allowed = user.allowed_groups()
        tools = self.registry.definitions(allowed)
        system = system if system is not None else self.system_prompt(user)
        if s.model.startswith("gemini"):
            answer = self.gemini.create(model=s.model, system=system, messages=messages, tools=tools,
                                        max_tokens=s.max_tokens, effort=s.effort)
            usage.record(self.store, answer, s.model)
            return answer
        tools = [t for t in tools if t["name"] not in gemini.ONLY_FOR_GEMINI]
        if allowed is None or "web" in allowed:
            tools += SERVER_TOOLS
        try:
            answer = self._ask_claude(messages, system, tools)
            usage.record(self.store, answer, s.model)
            return answer
        except Exception as exc:
            if not gemini.available() or not claude_unusable(exc):
                raise
            # No credit or no key for Claude: Gemini (free) takes over instead of failing.
            log.warning("Claude is unavailable, switching to Gemini: %s", exc)
            s.model = gemini.DEFAULT_MODEL
            return self._request(messages, user, system, allowed)

    def _ask_claude(self, messages: list, system, tools: list) -> object:
        s = self.settings
        kwargs = dict(
            model=s.model,
            max_tokens=s.max_tokens,
            system=system,
            messages=gemini.for_claude(messages),
            tools=tools,
            output_config={"effort": s.effort},
            # Tools and the system prompt are the same on every step, so the API reads them from its
            # cache instead of processing them again; automatic caching also covers the growing conversation.
            cache_control={"type": "ephemeral"},
        )
        if s.refusal_fallback and not s.model.startswith("claude-haiku"):  # Haiku has no server-side fallback
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            return self.client.beta.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            if "cache_control" in str(exc) and kwargs.pop("cache_control", None):
                log.warning("retrying without automatic caching: %s", exc)  # caching only saves time
                return self.client.beta.messages.create(**kwargs)
            # Thinking blocks are signed against the exact request they came from. If the API
            # refuses one (e.g. after a fallback model answered), drop them and try once more.
            if "thinking" not in str(exc) or not strip_thinking(messages):
                raise
            log.warning("retrying without earlier thinking blocks: %s", exc)
            kwargs["messages"] = gemini.for_claude(messages)
            return self.client.beta.messages.create(**kwargs)

    def ask(
        self,
        text: str,
        conversation: str = "main",
        confirmer: Confirmer | None = None,
        user=None,
        on_progress: Callable[[str], None] | None = None,
        images: list[tuple[str, str]] | None = None,
    ) -> str:
        """Handle one user message end to end and return Jarvis's final answer.

        ``user`` (an accounts.User) limits the tools to that account's permissions. ``images`` are
        (media type, base64) pairs, e.g. a frame of the screen or the camera the user is sharing.
        """
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
        if images:
            blocks = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}} for mt, data in images]
            note = "(Live frames of what I am sharing right now: my screen and/or my camera.)\n\n"
            messages[-1]["content"] = blocks + [{"type": "text", "text": note + messages[-1]["content"]}]

        answer = self._loop(messages, confirmer or self.confirmer, user, on_progress or self.on_progress)
        self.store.add_message(conversation, "user", text)
        self.store.add_message(conversation, "assistant", answer)
        return answer

    def _loop(self, messages: list, confirmer: Confirmer, user=None, on_progress=None, persona: str = "",
              groups: set | None = None, agent: str = "Jarvis", depth: int = 0) -> str:
        """The tool loop. Specialists (see plugins/team.py) pass a ``persona``, the permission
        ``groups`` they work with (never more than the user's own) and their ``agent`` name."""
        on_progress = on_progress or self.on_progress
        allowed = user.allowed_groups() if user is not None else None
        ask_groups = user.ask_groups() if user is not None else None
        if groups is not None:
            allowed = (set(groups) if allowed is None else allowed & set(groups)) | {"core"}
        # Fixed for the whole turn (thinking blocks are bound to it). The cache breakpoint sits before the
        # clock, so tools, personality and memories are cached from one message to the next.
        system = [{"type": "text", "text": self._base_prompt(user) + persona, "cache_control": {"type": "ephemeral"}},
                  {"type": "text", "text": self._clock().strip()}]
        TURN.set({"user": user, "confirmer": confirmer, "on_progress": on_progress, "depth": depth})
        texts: list[str] = []
        for _ in range(self.settings.max_tool_rounds):
            response = self._request(messages, user, system, allowed)
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
                on_progress(block.name)
                trust_local = self.settings.trust_local_actions and (user is None or user.is_owner)
                output, is_error = self.registry.run(
                    block.name, dict(block.input or {}), confirmer, trust_local, allowed, ask_groups
                )
                log.info("tool %s -> %s", block.name, output[:200] if isinstance(output, str) else "[image]")
                self.log_activity(user, agent, block.name, dict(block.input or {}), is_error)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        else:
            texts.append("(Спрях: твърде много стъпки за една заявка.)")
        return "\n\n".join(texts) if texts else "Готово."

    def log_activity(self, user, agent: str, name: str, args: dict, is_error: bool) -> None:
        """Everything Jarvis does goes to the activity feed (Settings > Activity)."""
        tool = self.registry.tools.get(name)
        try:
            summary = tool.describe_call(args) if tool else name
        except Exception:
            summary = name
        try:
            self.store.insert("activity", user_id=getattr(user, "id", None), agent=agent, tool=name,
                              summary=str(summary)[:600], ok=0 if is_error else 1)
        except Exception:
            log.exception("activity log failed")

    def run_agent(self, task: str, persona: str, groups: set, agent: str) -> str:
        """Run one specialist on ``task`` for the person whose request is being answered."""
        turn = TURN.get() or {}
        if turn.get("depth", 0) >= 1:
            raise RuntimeError("Specialists cannot hand work to other specialists; do it yourself.")
        messages = [{"role": "user", "content": task}]
        return contextvars.copy_context().run(self._loop, messages, turn.get("confirmer") or self.confirmer, turn.get("user"), turn.get("on_progress"),
                          persona=persona, groups=set(groups) - {"team"}, agent=agent, depth=turn.get("depth", 0) + 1)
