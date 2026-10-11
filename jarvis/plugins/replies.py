"""„Отговаряй вместо мен“: replies to the messages that reach the owner's Android phone.

From a video of an assistant that answers Instagram messages by itself. Here it stays the owner's call:
Jarvis watches the phone's notifications (WhatsApp, Viber, Messenger, Instagram, Telegram, Signal, SMS)
over adb, with no AI and no cost while nothing new comes in. For each new message the cheapest model
writes a short reply in the owner's style (JARVIS_REPLY_STYLE and the replies they approved before), and
the app asks "send it?". Only after their "да" does Jarvis open the message from the notification on the
phone, type the reply and press Send, like the owner would. Group chats, codes and ads are left alone.

Switch it on with „отговаряй вместо мен“ (off: „спри да отговаряш“) or JARVIS_AUTO_REPLY=1.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

from ..tools import ToolRegistry, obj
from . import save_settings

log = logging.getLogger("jarvis.replies")

APPS = {
    "com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp", "com.viber.voip": "Viber",
    "com.facebook.orca": "Messenger", "com.instagram.android": "Instagram", "org.telegram.messenger": "Telegram",
    "org.thoughtcrime.securesms": "Signal", "com.google.android.apps.messaging": "SMS", "com.android.mms": "SMS",
    "com.samsung.android.messaging": "SMS",
}
SKIP = "SKIP"
SEND = re.compile(r"^(?:send|изпрати|изпращане)\b", re.I)
SUMMARY = re.compile(r"^\d+\s+(?:new\s+)?(?:messages|съобщения|нови съобщения)", re.I)
pause = time.sleep
REPLY_PROMPT = """You write the reply that {user} will send from their own phone, as {user} (first person, never as \
an assistant and never mentioning AI). Write exactly what they would send: short (one or two sentences), natural, \
in the language of the message and in their style.
{style}
Never agree to money, payments, meetings or plans, and never share private details: when the message needs a \
decision only {user} can make, write a short holding reply (for example "Ще ти пиша малко по-късно.").
If no reply is needed (ads, codes, delivery or bank notices, "ok", a sticker or emoji only), answer exactly SKIP.
Answer with the reply text only."""


def enabled() -> bool:
    return os.environ.get("JARVIS_AUTO_REPLY", "").strip().lower() in ("1", "true", "yes", "on", "да")


def _value(block: str, name: str) -> str:
    """One extra of a notification as dumpsys prints it: ``android.title=String (Мария)``."""
    found = re.search(rf"^\s*android\.{name}=(.*)$", block, re.M)
    if not found:
        return ""
    value = found[1].strip()
    if value == "null" or "[length=" in value:  # empty, or hidden without --noredact
        return ""
    typed = re.fullmatch(r"[A-Z][A-Za-z]+ \((.*)\)?", value)
    return (typed[1].removesuffix(")") if typed else value).strip()


def messages_in(dump: str) -> list[dict]:
    """The chat messages among the phone's notifications (from ``dumpsys notification --noredact``)."""
    out = []
    for block in dump.split("NotificationRecord(")[1:]:
        pkg = re.search(r"pkg=(\S+)", block)
        app = APPS.get(pkg[1]) if pkg else None
        if not app:
            continue
        sender, text = _value(block, "title"), _value(block, "text")
        group = re.search(r"android\.isGroupConversation=\w+ \(true\)", block) or _value(block, "conversationTitle")
        if not sender or not text or group or sender in APPS.values() or SUMMARY.match(text):
            continue
        out.append({"app": app, "package": pkg[1], "sender": sender[:80], "text": text[:600],
                    "key": f"{pkg[1]}|{sender}|{text}"[:900]})
    return list({m["key"]: m for m in out}.values())


def summary(message: dict, reply: str) -> str:
    return f"ОТГОВОР в {message['app']} до {message['sender']}\nПиса: „{message['text']}“\nОтговор: „{reply}“"


class ReplyWatcher:
    """Checks the phone's notifications every ``interval`` seconds while „отговаряй вместо мен“ is on."""

    def __init__(self, ctx, interval: float = 45.0):
        self.ctx = ctx
        self.interval = interval
        self.ready = False  # the first look after switching on only notes what is already there
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="jarvis-replies", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("checking the phone's messages failed")

    # --- the phone ----------------------------------------------------------------------------------
    def phone(self):
        android = getattr(self.ctx, "android", None)
        devices = android.devices() if android else {}
        if not devices:
            return None, None
        return android, "phone" if "phone" in devices else next(iter(devices))

    def read(self) -> list[dict] | None:
        android, device = self.phone()
        if not android:
            return None
        return messages_in(android.shell(device, "dumpsys notification --noredact", timeout=20))

    # --- what is new --------------------------------------------------------------------------------
    def fresh(self, messages: list[dict], with_old: bool = False) -> list[dict]:
        since = (datetime.now() - timedelta(days=7)).replace(microsecond=0).isoformat()
        rows = self.ctx.store.query("SELECT key, state FROM inbox WHERE created >= ?", (since,))
        seen = {r["key"] for r in rows if not (with_old and r["state"] == "old")}
        return [m for m in messages if m["key"] not in seen]

    def note(self, message: dict, state: str, reply: str | None = None) -> int:
        return self.ctx.store.insert("inbox", app=message["app"], sender=message["sender"], text=message["text"],
                                     key=message["key"], reply=reply, state=state)

    def tick(self) -> int:
        """One look at the phone. Returns how many new messages got a reply to approve."""
        if not enabled():
            self.ready = False
            return 0
        from .tasks import in_quiet_hours

        if in_quiet_hours(self.ctx.settings.quiet_hours, datetime.now()):
            return 0
        try:
            messages = self.read()
        except Exception as exc:  # the phone is away or asleep: try again next time
            log.info("could not read the phone's notifications: %s", exc)
            return 0
        if messages is None:
            return 0
        if not self.ready:
            for message in self.fresh(messages):
                self.note(message, "old")
            self.ready = True
            return 0
        return self.answer(self.fresh(messages))

    def check(self) -> str:
        """Asked by the owner: reply to everything waiting on the phone now, also what was there before."""
        messages = self.read()
        if messages is None:
            return "Няма свързан телефон. Включи го с USB кабел (или по Wi-Fi) с включен USB debugging."
        waiting = self.fresh(messages, with_old=True)
        for message in waiting:
            self.ctx.store.execute("DELETE FROM inbox WHERE state='old' AND key=?", (message["key"],))
        asked = self.answer(waiting)
        if not messages:
            return "На телефона няма нови съобщения."
        if not asked:
            return "Новите съобщения на телефона не искат отговор (реклами, кодове или групи)."
        return f"Написах {asked} {'отговор' if asked == 1 else 'отговора'}. Всеки чака твоето „да“, преди да го пратя."

    # --- a reply ------------------------------------------------------------------------------------
    def answer(self, messages: list[dict], limit: int = 5) -> int:
        asked = 0
        for message in messages[:limit]:
            try:
                reply = self.draft(message)
            except Exception:
                log.exception("could not write a reply")
                continue
            if not reply:
                self.note(message, "skipped")
                continue
            row = self.note(message, "asked", reply)
            asked += 1
            threading.Thread(target=self.ask_and_send, args=(row, message, reply), name="jarvis-reply",
                             daemon=True).start()
        return asked

    def draft(self, message: dict) -> str | None:
        jarvis = self.ctx.jarvis
        s = self.ctx.settings
        style = os.environ.get("JARVIS_REPLY_STYLE", "").strip()
        sent = self.ctx.store.query("SELECT reply FROM inbox WHERE state='sent' ORDER BY id DESC LIMIT 6")
        lines = [f"Their style, in their words: {style}" if style else ""]
        if sent:
            lines.append("Replies they sent before (match this tone and length):\n"
                         + "\n".join(f"- {r['reply']}" for r in sent))
        system = REPLY_PROMPT.format(user=s.user_name, style="\n".join(x for x in lines if x)) + jarvis._clock()
        said = f"{message['app']} message from {message['sender']}:\n{message['text']}"
        response = jarvis._request([{"role": "user", "content": said}], None, system, set(), model=jarvis.fast_model(),
                                   effort="low", tools=[], max_tokens=300)
        text = "\n".join(b.text for b in response.content if b.type == "text").strip().strip('"„“').strip()
        return None if not text or SKIP in text[:12].upper() else text[:500]

    def ask_and_send(self, row: int, message: dict, reply: str) -> None:
        hub = getattr(self.ctx, "hub", None)
        if hub is None:
            self.ctx.notify(f"Чернова за отговор (отвори приложението на Jarvis, за да я пратиш):\n{summary(message, reply)}")
            self.ctx.store.execute("UPDATE inbox SET state='declined' WHERE id=?", (row,))
            return
        args = {"app": message["app"], "to": message["sender"], "message": message["text"], "text": reply}
        output, failed = self.ctx.jarvis.registry.run("phone_reply", args, lambda what: hub.confirmer(what))
        declined = failed and output.startswith("The user declined")
        state = "declined" if declined else "failed" if failed else "sent"
        self.ctx.store.execute("UPDATE inbox SET state=? WHERE id=?", (state, row))
        if state == "failed":
            reason = output.partition(": ")[2] or output
            self.ctx.notify(f"Не успях да пратя отговора до {message['sender']} в {message['app']}: {reason}")


def send(android, device: str, message: dict, text: str) -> str:
    """Open the message from the phone's notifications, type the reply and press Send."""
    android.shell(device, "input keyevent KEYCODE_WAKEUP")
    for _ in range(12):  # approved on the laptop with the phone locked: a minute to unlock it
        lock = android.shell(device, "dumpsys window | grep -E 'Lockscreen=|KeyguardShowing='")
        if not re.search(r"(?:Lockscreen|KeyguardShowing)=true", lock):
            break
        pause(5)
    else:
        raise RuntimeError("телефонът е заключен. Отключи го и отговори сам, или ми кажи пак")
    android.shell(device, "cmd statusbar expand-notifications")
    pause(1.2)
    items = android.elements(device)
    needle = message["text"][:40].casefold()
    hit = (next((e for e in items if needle and needle in e["label"].casefold()), None)
           or next((e for e in items if e["label"].casefold() == message["sender"].casefold()), None))
    if hit is None:
        android.shell(device, "cmd statusbar collapse")
        raise RuntimeError("съобщението вече не е в известията (може би си го прочел)")
    android.shell(device, f"input tap {hit['x']} {hit['y']}")
    pause(2.0)
    field = next((e for e in android.elements(device) if e["field"]), None)
    if field is None:
        raise RuntimeError(f"{message['app']} се отвори, но не намерих къде да пиша")
    android.shell(device, f"input tap {field['x']} {field['y']}")
    pause(0.5)
    android.type_text(device, text)
    pause(0.6)
    buttons = [e for e in android.elements(device) if SEND.match(e["label"]) and not e["field"]]
    if not buttons:
        raise RuntimeError(f"написах отговора в {message['app']}, но не намерих бутона за изпращане; натисни го на телефона")
    button = min(buttons, key=lambda e: (not e["tap"], len(e["label"])))
    android.shell(device, f"input tap {button['x']} {button['y']}")
    pause(1.0)
    android.shell(device, "input keyevent KEYCODE_HOME")
    return f"Пратих на {message['sender']} в {message['app']}."


def register(registry: ToolRegistry, ctx) -> None:
    ctx.replies = watcher = ReplyWatcher(ctx)

    @registry.tool(
        "„Отговаряй вместо мен“: Jarvis watches new chat messages on the owner's Android phone (WhatsApp, Viber, "
        "Messenger, Instagram, Telegram, Signal, SMS), writes a reply in the owner's style and sends it only after "
        "their yes. Actions: on · off · status · check (reply now to what is waiting on the phone).",
        obj({"action": ("string", "on | off | status | check")}),
    )
    def auto_reply(action: str):
        if action == "on":
            save_settings(ctx, {"JARVIS_AUTO_REPLY": "1"})
            watcher.ready = False
            phone = "" if watcher.phone()[0] else (" Телефонът още не е свързан: включи го с USB кабел с включен "
                                                   "USB debugging и започвам.")
            return ("Добре. Ще гледам новите съобщения на телефона и ще ти предлагам отговор в твой стил. "
                    "Нищо не пращам без твоето „да“." + phone)
        if action == "off":
            save_settings(ctx, {"JARVIS_AUTO_REPLY": "0"})
            return "Спрях да отговарям вместо теб."
        if action == "check":
            return watcher.check()
        rows = ctx.store.query("SELECT app, sender, reply, state FROM inbox WHERE state != 'old' ORDER BY id DESC LIMIT 5")
        words = {"sent": "пратен", "declined": "отказан", "failed": "не стана", "asked": "чака „да“", "skipped": "без отговор"}
        recent = "\n".join(f"- {r['sender']} ({r['app']}): {words.get(r['state'], r['state'])}"
                           + (f" · „{r['reply']}“" if r["reply"] else "") for r in rows)
        return ("Отговарям вместо теб: включено." if enabled() else "Отговарям вместо теб: изключено.") + (
            "\nПоследни:\n" + recent if recent else "")

    @registry.tool(
        "Reply to a chat message that is among the notifications of the owner's Android phone: opens it, types "
        "the text and presses Send in that app (WhatsApp, Viber, Messenger, Instagram, Telegram, Signal or SMS).",
        obj({"app": ("string", "The app, e.g. WhatsApp"), "to": ("string", "Who wrote (as the notification shows)"),
             "message": ("string", "Their message being answered"), "text": ("string", "The reply to send")}),
        confirm=True,
        summarize=lambda a: summary({"app": a.get("app"), "sender": a.get("to"), "text": a.get("message", "")},
                                    a.get("text", "")),
    )
    def phone_reply(app: str, to: str, message: str, text: str):
        android, device = watcher.phone()
        if not android:
            raise RuntimeError("No phone is connected. Plug it in by USB with USB debugging on.")
        return send(android, device, {"app": app, "sender": to, "text": message}, text)
