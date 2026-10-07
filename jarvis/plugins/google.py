"""Google Calendar and Gmail, directly on the owner's Google account.

One-time setup: create an OAuth client ("Desktop app") in Google Cloud Console with the
Calendar and Gmail APIs enabled, download its JSON, set GOOGLE_CLIENT_SECRET to its path and
run `jarvis google-login` (a browser opens to sign in). The token is kept in ~/.jarvis.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from email.message import EmailMessage

from ..tools import ToolRegistry, obj
from . import NotConfigured

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
]


def token_path(settings):
    return settings.home / "google_token.json"


def login(settings) -> None:
    secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not secret:
        raise NotConfigured("Google", ["GOOGLE_CLIENT_SECRET (path to the OAuth client JSON)"])
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = InstalledAppFlow.from_client_secrets_file(secret, SCOPES).run_local_server(port=0)
    token_path(settings).write_text(creds.to_json(), encoding="utf-8")
    print("Готово, Jarvis има достъп до Google Calendar и Gmail.")


def service(settings, name: str, version: str):
    path = token_path(settings)
    if not path.exists():
        raise NotConfigured("Google", ["GOOGLE_CLIENT_SECRET, then run `jarvis google-login`"])
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return build(name, version, credentials=creds, cache_discovery=False)


def _rfc3339(value: str) -> str:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat()


def register(registry: ToolRegistry, ctx) -> None:
    s = ctx.settings

    @registry.tool(
        "List events from the owner's Google Calendar (default: the next 7 days).",
        obj({"start?": ("string", "From, ISO 8601"), "end?": ("string", "To, ISO 8601"), "query?": ("string", "Text search")}),
    )
    def google_calendar_list(start: str | None = None, end: str | None = None, query: str | None = None):
        start = start or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end = end or (datetime.fromisoformat(start) + timedelta(days=7)).isoformat()
        items = service(s, "calendar", "v3").events().list(
            calendarId="primary", timeMin=_rfc3339(start), timeMax=_rfc3339(end), q=query,
            singleEvents=True, orderBy="startTime", maxResults=100,
        ).execute().get("items", [])
        return [
            {"id": e["id"], "title": e.get("summary"), "start": e["start"].get("dateTime", e["start"].get("date")),
             "end": e["end"].get("dateTime", e["end"].get("date")), "location": e.get("location"),
             "attendees": [a.get("email") for a in e.get("attendees", [])]}
            for e in items
        ]

    @registry.tool(
        "Add an event to the owner's Google Calendar (no invitations are sent).",
        obj({
            "title": ("string", "Title"), "start": ("string", "Start, ISO 8601"), "end?": ("string", "End, ISO 8601"),
            "location?": ("string", "Where"), "notes?": ("string", "Description"),
            "reminder_minutes?": ("integer", "Popup reminder before the event"),
        }),
    )
    def google_calendar_add(title: str, start: str, end: str | None = None, location: str | None = None,
                            notes: str | None = None, reminder_minutes: int | None = None):
        end = end or (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        body = {"summary": title, "location": location, "description": notes,
                "start": {"dateTime": _rfc3339(start)}, "end": {"dateTime": _rfc3339(end)}}
        if reminder_minutes is not None:
            body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": reminder_minutes}]}
        ev = service(s, "calendar", "v3").events().insert(calendarId="primary", body=body).execute()
        return f"Added: {ev.get('htmlLink')}"

    @registry.tool(
        "Invite people to a Google Calendar meeting (they receive e-mail invitations).",
        obj({
            "title": ("string", "Title"), "start": ("string", "Start, ISO 8601"), "end": ("string", "End, ISO 8601"),
            "attendees": ("array", "E-mail addresses"), "location?": ("string", "Where or video link"),
            "notes?": ("string", "Description"), "video_call?": ("boolean", "Add a Google Meet link"),
        }),
        confirm=True,
        summarize=lambda a: f"ПОКАНА „{a.get('title')}“ {a.get('start')} до {', '.join(a.get('attendees') or [])}",
    )
    def google_calendar_invite(title: str, start: str, end: str, attendees: list, location: str | None = None,
                               notes: str | None = None, video_call: bool = False):
        body = {"summary": title, "location": location, "description": notes,
                "start": {"dateTime": _rfc3339(start)}, "end": {"dateTime": _rfc3339(end)},
                "attendees": [{"email": a} for a in attendees]}
        if video_call:
            body["conferenceData"] = {"createRequest": {"requestId": f"jarvis-{datetime.now().timestamp()}"}}
        ev = service(s, "calendar", "v3").events().insert(
            calendarId="primary", body=body, sendUpdates="all", conferenceDataVersion=1 if video_call else 0,
        ).execute()
        return f"Invitations sent: {ev.get('htmlLink')} {ev.get('hangoutLink') or ''}".strip()

    @registry.tool(
        "Delete an event from Google Calendar.",
        obj({"event_id": ("string", "Event id from google_calendar_list")}),
        confirm=True,
        summarize=lambda a: f"Изтрий събитие {a.get('event_id')} от Google Calendar",
    )
    def google_calendar_delete(event_id: str):
        service(s, "calendar", "v3").events().delete(calendarId="primary", eventId=event_id).execute()
        return "Deleted."

    @registry.tool(
        "Search Gmail with Gmail search syntax (e.g. 'is:unread', 'from:bank newer_than:7d', 'subject:фактура') "
        "and read the matching messages.",
        obj({"query?": ("string", "Gmail search (default: is:unread in:inbox)"), "limit?": ("integer", "Max messages (default 10)")}),
    )
    def gmail_search(query: str = "is:unread in:inbox", limit: int = 10):
        gm = service(s, "gmail", "v1")
        ids = gm.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
        out = []
        for m in ids:
            msg = gm.users().messages().get(userId="me", id=m["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            out.append({"id": m["id"], "from": headers.get("from"), "subject": headers.get("subject"),
                        "date": headers.get("date"), "text": _plain_text(msg["payload"])[:3000] or msg.get("snippet")})
        return out

    @registry.tool(
        "Send an e-mail from the owner's Gmail.",
        obj({"to": ("string", "Address(es), comma-separated"), "subject": ("string", "Subject"), "body": ("string", "Text"),
             "reply_to_id?": ("string", "Gmail message id to reply to (keeps the thread)")}),
        confirm=True,
        summarize=lambda a: f"GMAIL до {a.get('to')}, тема „{a.get('subject')}“:\n{a.get('body')}",
    )
    def gmail_send(to: str, subject: str, body: str, reply_to_id: str | None = None):
        gm = service(s, "gmail", "v1")
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        payload = {}
        if reply_to_id:
            orig = gm.users().messages().get(userId="me", id=reply_to_id, format="metadata",
                                             metadataHeaders=["Message-ID"]).execute()
            mid = next((h["value"] for h in orig["payload"]["headers"] if h["name"].lower() == "message-id"), None)
            if mid:
                msg["In-Reply-To"] = msg["References"] = mid
            payload["threadId"] = orig["threadId"]
        payload["raw"] = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = gm.users().messages().send(userId="me", body=payload).execute()
        return f"Sent (id {sent['id']})."

    @registry.tool(
        "Mark Gmail messages as read, archive them, or move them to trash.",
        obj({"ids": ("array", "Message ids"), "action": ("string", "read | archive | trash")}),
    )
    def gmail_tidy(ids: list, action: str):
        gm = service(s, "gmail", "v1")
        for mid in ids:
            if action == "trash":
                gm.users().messages().trash(userId="me", id=mid).execute()
            else:
                labels = ["UNREAD"] if action == "read" else ["INBOX"]
                gm.users().messages().modify(userId="me", id=mid, body={"removeLabelIds": labels}).execute()
        return f"{action}: {len(ids)} messages."


def _plain_text(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_text(part)
        if text:
            return text
    return ""
