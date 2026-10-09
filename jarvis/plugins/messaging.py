"""WhatsApp, Viber and Telegram messages to other people, plus push notifications to the owner.

- WhatsApp: through WhatsApp Web in Jarvis's own browser (scan the QR code once with
  `jarvis` → "open web.whatsapp.com"), or through the WhatsApp Cloud API when
  WHATSAPP_TOKEN and WHATSAPP_PHONE_ID are set.
- Viber: through the owner's Android phone (adb), which opens the chat pre-filled.
- Push: ntfy.sh (free app for iPhone and Android) when NTFY_TOPIC is set.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from ..tools import ToolRegistry, obj


def push_notify(text: str, title: str = "Jarvis") -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    req = urllib.request.Request(f"{server}/{topic}", data=text.encode(), headers={"Title": title.encode("utf-8").decode("latin-1", "ignore") or "Jarvis"})
    urllib.request.urlopen(req, timeout=15).close()


def register(registry: ToolRegistry, ctx) -> None:
    if os.environ.get("NTFY_TOPIC"):
        ctx.notifiers.append(push_notify)

    def number_for(to: str) -> str:
        digits = to.replace(" ", "")
        if digits.lstrip("+").isdigit():
            return digits
        rows = ctx.store.query("SELECT phone FROM contacts WHERE casefold(name) LIKE casefold(?)", (f"%{to}%",))
        phones = [r["phone"] for r in rows if r["phone"]]
        if len(phones) != 1:
            raise ValueError(f"Need exactly one phone number for '{to}', found {phones or 'none'}.")
        return phones[0]

    @registry.tool(
        "Send a WhatsApp message (to a number or contact name).",
        obj({"to": ("string", "Phone number with country code, or contact name"), "text": ("string", "Message")}),
        confirm=True,
        summarize=lambda a: f"WHATSAPP до {a.get('to')}: „{a.get('text')}“",
    )
    def whatsapp_send(to: str, text: str):
        number = number_for(to).lstrip("+")
        token, phone_id = os.environ.get("WHATSAPP_TOKEN"), os.environ.get("WHATSAPP_PHONE_ID")
        if token and phone_id:
            req = urllib.request.Request(
                f"https://graph.facebook.com/v21.0/{phone_id}/messages",
                data=json.dumps({"messaging_product": "whatsapp", "to": number, "type": "text", "text": {"body": text}}).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return f"Sent via WhatsApp API: {resp.read().decode()[:200]}"
        if not hasattr(ctx, "browser"):
            raise RuntimeError("No WhatsApp route: set WHATSAPP_TOKEN/WHATSAPP_PHONE_ID or install the browser extra.")
        url = f"https://web.whatsapp.com/send?phone={number}&text={urllib.parse.quote(text)}"

        def send(page):
            page.goto(url, wait_until="domcontentloaded")
            box = page.locator("footer div[contenteditable='true']")
            box.wait_for(timeout=60000)  # the first time, scan the QR code shown in the browser window
            page.wait_for_timeout(1000)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
            return "Sent via WhatsApp Web."

        return ctx.browser().do(send, timeout=120)

    @registry.tool(
        "Send a Viber message through the owner's Android phone: opens the chat with the text; "
        "then use android look/tap to press Send.",
        obj({"device": ("string", "Phone device name"), "to": ("string", "Number or contact"), "text": ("string", "Message")}),
        confirm=True,
        summarize=lambda a: f"VIBER до {a.get('to')}: „{a.get('text')}“",
    )
    def viber_send(device: str, to: str, text: str):
        from .android import adb, devices_from_env

        serial = devices_from_env()[device]
        number = urllib.parse.quote(number_for(to))
        adb(serial, "shell", f"am start -a android.intent.action.VIEW -d 'viber://chat?number={number}&draft={urllib.parse.quote(text)}'")
        return "Viber chat opened with the message. Look at the phone screen and tap Send."

    @registry.tool(
        "Send a push notification to the owner's phone (ntfy app).",
        obj({"text": ("string", "Message"), "title?": ("string", "Title")}),
    )
    def push_to_phone(text: str, title: str = "Jarvis"):
        if not os.environ.get("NTFY_TOPIC"):
            from . import NotConfigured

            raise NotConfigured("Push notifications", ["NTFY_TOPIC (install the ntfy app and subscribe to that topic)"])
        push_notify(text, title)
        return "Sent."

