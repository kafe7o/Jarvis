"""Phone calls and SMS (Twilio), and e-mail (SMTP/IMAP).

Every action here that reaches another person asks the user first, every time.
"""

from __future__ import annotations

import email
import imaplib
import os
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage
from xml.sax.saxutils import escape

from ..tools import ToolRegistry, obj
from . import NotConfigured

TWILIO_VARS = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"]


def twilio_client(settings):
    if not (settings.twilio_sid and settings.twilio_token and settings.twilio_number):
        raise NotConfigured("Twilio (phone calls/SMS)", TWILIO_VARS)
    from twilio.rest import Client

    return Client(settings.twilio_sid, settings.twilio_token)


def voice_attrs() -> str:
    voice = os.environ.get("TWILIO_VOICE", "Google.bg-BG-Standard-A")
    lang = os.environ.get("TWILIO_LANGUAGE", "bg-BG")
    return f'voice="{escape(voice)}" language="{escape(lang)}"'


def say_twiml(message: str, repeat: int = 1) -> str:
    from ..phone_agent import neural_audio

    url = neural_audio(message, os.environ.get("TWILIO_LANGUAGE", "bg-BG"))  # Jarvis's own voice when possible
    speech = f"<Play>{escape(url)}</Play>" if url else f"<Say {voice_attrs()}>{escape(message)}</Say>"
    says = "".join(f'{speech}<Pause length="1"/>' for _ in range(max(1, repeat)))
    return f"<Response>{says}</Response>"


def send_sms_raw(settings, to: str | None, body: str) -> str:
    if not to:
        raise NotConfigured("Owner phone", ["JARVIS_OWNER_PHONE"])
    msg = twilio_client(settings).messages.create(to=to, from_=settings.twilio_number, body=body)
    return msg.sid


def call_raw(settings, to: str | None, message: str, repeat: int = 2) -> str:
    if not to:
        raise NotConfigured("Owner phone", ["JARVIS_OWNER_PHONE"])
    call = twilio_client(settings).calls.create(to=to, from_=settings.twilio_number, twiml=say_twiml(message, repeat))
    return call.sid


def register(registry: ToolRegistry, ctx) -> None:
    s = ctx.settings
    store = ctx.store

    def resolve(to: str, field: str = "phone") -> str:
        """Accept a phone number/e-mail, or a contact name."""
        candidate = to.strip()
        if field == "phone" and (candidate.startswith("+") or candidate.replace(" ", "").isdigit()):
            return candidate.replace(" ", "")
        if field == "email" and "@" in candidate:
            return candidate
        exact = store.query(f"SELECT {field} FROM contacts WHERE casefold(name)=casefold(?)", (candidate,))
        rows = exact or store.query(f"SELECT {field} FROM contacts WHERE casefold(name) LIKE casefold(?)", (f"%{candidate}%",))
        values = [r[field] for r in rows if r[field]]
        if len(values) == 1:
            return values[0]
        if not values:
            raise ValueError(f"No {field} found for '{to}'. Save the contact first or give the exact {field}.")
        raise ValueError(f"Several contacts match '{to}': {values}. Be more specific.")

    def who(to: str, field: str = "phone") -> str:
        try:
            resolved = resolve(to, field)
        except Exception:
            return to
        return to if resolved == to else f"{to} ({resolved})"

    # Phone -----------------------------------------------------------------
    @registry.tool(
        "Phone someone and have Jarvis say a message out loud (one-way announcement).",
        obj({
            "to": ("string", "Phone number (E.164) or contact name"),
            "message": ("string", "What to say, in the language the callee speaks"),
            "repeat?": ("integer", "How many times to repeat the message (default 2)"),
        }),
        confirm=True,
        summarize=lambda a: f"ОБАЖДАНЕ до {who(a.get('to', ''))}, ще кажа: „{a.get('message')}“",
    )
    def make_call(to: str, message: str, repeat: int = 2):
        sid = call_raw(s, resolve(to), message, repeat)
        return f"Calling, call id {sid}. Use call_status to follow it."

    @registry.tool(
        "Connect the owner with someone: Twilio rings the owner's phone first, then dials the other person "
        "and bridges the two so the owner can talk to them directly.",
        obj({"to": ("string", "Phone number (E.164) or contact name")}),
        confirm=True,
        summarize=lambda a: f"СВЪРЖИ ме по телефона с {who(a.get('to', ''))}",
    )
    def connect_call(to: str):
        if not s.owner_phone:
            raise NotConfigured("Owner phone", ["JARVIS_OWNER_PHONE"])
        target = resolve(to)
        twiml = (
            f"<Response><Say {voice_attrs()}>Свързвам ви.</Say>"
            f'<Dial callerId="{escape(s.twilio_number or "")}"><Number>{escape(target)}</Number></Dial></Response>'
        )
        call = twilio_client(s).calls.create(to=s.owner_phone, from_=s.twilio_number, twiml=twiml)
        return f"Ringing your phone first, then {target}. Call id {call.sid}."

    @registry.tool(
        "Have Jarvis make a phone call and hold a real two-way conversation with the person to achieve a goal "
        "(book a table, ask a question, reschedule an appointment). Jarvis reports back what was agreed. "
        "Needs JARVIS_PUBLIC_URL pointing at the running Jarvis phone server.",
        obj({
            "to": ("string", "Phone number (E.164) or contact name"),
            "goal": ("string", "What the call must achieve, with every detail Jarvis may share"),
            "language?": ("string", "Language of the call, e.g. bg-BG, en-US (default bg-BG)"),
        }),
        confirm=True,
        summarize=lambda a: f"РАЗГОВОР по телефона с {who(a.get('to', ''))}, цел: {a.get('goal')}",
    )
    def agent_call(to: str, goal: str, language: str = "bg-BG"):
        from ..phone_agent import start_agent_call

        return start_agent_call(ctx, resolve(to), goal, language)

    @registry.tool("Get the status of a call (and the transcript of an agent_call).", obj({"call_id": ("string", "Call id")}))
    def call_status(call_id: str):
        from ..phone_agent import transcript_for

        call = twilio_client(s).calls(call_id).fetch()
        info = {"status": call.status, "duration_s": call.duration, "to": call.to}
        transcript = transcript_for(call_id)
        if transcript:
            info["transcript"] = transcript
        return info

    @registry.tool(
        "Send an SMS.",
        obj({"to": ("string", "Phone number (E.164) or contact name"), "body": ("string", "Message text")}),
        confirm=True,
        summarize=lambda a: f"SMS до {who(a.get('to', ''))}: „{a.get('body')}“",
    )
    def send_sms(to: str, body: str):
        return f"SMS sent (id {send_sms_raw(s, resolve(to), body)})."

    @registry.tool("List recent SMS messages received on the Jarvis number.", obj({"limit?": ("integer", "How many (default 10)")}))
    def read_sms(limit: int = 10):
        msgs = twilio_client(s).messages.list(to=s.twilio_number, limit=limit)
        return [{"from": m.from_, "body": m.body, "date": str(m.date_sent)} for m in msgs]

    # E-mail ----------------------------------------------------------------
    def smtp_ready():
        if not (s.smtp_host and s.email_user and s.email_password):
            raise NotConfigured("E-mail", ["JARVIS_SMTP_HOST", "JARVIS_EMAIL_USER", "JARVIS_EMAIL_PASSWORD"])

    @registry.tool(
        "Send an e-mail.",
        obj({
            "to": ("string", "Address or contact name; several separated by commas"),
            "subject": ("string", "Subject"),
            "body": ("string", "Plain-text body"),
            "attachments?": ("array", "File paths to attach"),
        }),
        confirm=True,
        summarize=lambda a: f"ИМЕЙЛ до {a.get('to')}, тема „{a.get('subject')}“:\n{a.get('body')}",
    )
    def send_email(to: str, subject: str, body: str, attachments: list | None = None):
        smtp_ready()
        import mimetypes
        from pathlib import Path

        msg = EmailMessage()
        msg["From"] = s.email_user
        msg["To"] = ", ".join(resolve(t, "email") for t in to.split(","))
        msg["Subject"] = subject
        msg.set_content(body)
        for path in attachments or []:
            p = Path(path).expanduser()
            mime = (mimetypes.guess_type(p.name)[0] or "application/octet-stream").split("/")
            msg.add_attachment(p.read_bytes(), maintype=mime[0], subtype=mime[1], filename=p.name)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(s.email_user, s.email_password)
            smtp.send_message(msg)
        return f"E-mail sent to {msg['To']}."

    @registry.tool(
        "Read recent e-mails from the inbox.",
        obj({"limit?": ("integer", "How many (default 10)"), "unread_only?": ("boolean", "Only unread (default true)")}),
    )
    def read_email(limit: int = 10, unread_only: bool = True):
        if not (s.imap_host and s.email_user and s.email_password):
            raise NotConfigured("E-mail inbox", ["JARVIS_IMAP_HOST", "JARVIS_EMAIL_USER", "JARVIS_EMAIL_PASSWORD"])
        with imaplib.IMAP4_SSL(s.imap_host) as imap:
            imap.login(s.email_user, s.email_password)
            imap.select("INBOX", readonly=True)
            _, data = imap.search(None, "UNSEEN" if unread_only else "ALL")
            ids = data[0].split()[-limit:]
            out = []
            for mid in reversed(ids):
                _, parts = imap.fetch(mid, "(BODY.PEEK[])")
                msg = email.message_from_bytes(parts[0][1])
                body = ""
                for part in msg.walk():
                    if part.get_content_type() == "text/plain" and not part.get_filename():
                        body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                        break
                out.append({
                    "from": str(make_header(decode_header(msg.get("From", "")))),
                    "subject": str(make_header(decode_header(msg.get("Subject", "")))),
                    "date": msg.get("Date"),
                    "body": body[:2000],
                })
            return out
