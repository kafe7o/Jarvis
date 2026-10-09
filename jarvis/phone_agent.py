"""Two-way phone conversations: Jarvis talks on the phone on the owner's behalf.

A small webhook server receives Twilio's speech recognition results and answers
with what Jarvis says next. It handles:
- outbound calls started by the ``agent_call`` tool, with a goal to achieve;
- inbound calls to the Twilio number, where Jarvis answers as a secretary and
  takes a message for the owner.

Twilio must reach this server from the internet: set JARVIS_PUBLIC_URL to a public
https address that forwards to JARVIS_PHONE_PORT (for example with ngrok), and point
the Twilio number's "A call comes in" webhook at <JARVIS_PUBLIC_URL>/voice/incoming.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from xml.sax.saxutils import escape

import anthropic

from .plugins import NotConfigured

log = logging.getLogger("jarvis.phone")

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string", "description": "What to say next on the phone"},
        "done": {"type": "boolean", "description": "True when the call should end after saying this"},
        "summary": {"type": "string", "description": "When done: what was achieved/agreed, for the owner"},
    },
    "required": ["say", "done", "summary"],
    "additionalProperties": False,
}


@dataclass
class CallSession:
    goal: str
    language: str
    inbound: bool = False
    call_sid: str | None = None
    transcript: list[dict] = field(default_factory=list)
    summary: str = ""
    finished: bool = False
    silences: int = 0


SESSIONS: dict[str, CallSession] = {}
_SERVER: dict = {}


def transcript_for(call_sid: str) -> list[dict] | None:
    for sess in SESSIONS.values():
        if sess.call_sid == call_sid:
            return sess.transcript + ([{"summary": sess.summary}] if sess.summary else [])
    return None


def public_url() -> str:
    url = os.environ.get("JARVIS_PUBLIC_URL", "").rstrip("/")
    if not url:
        raise NotConfigured("Phone conversations", ["JARVIS_PUBLIC_URL (public https URL forwarding to JARVIS_PHONE_PORT)"])
    return url


def start_agent_call(ctx, to: str, goal: str, language: str = "bg-BG") -> str:
    from .plugins.comms import twilio_client

    base = public_url()
    ensure_server(ctx)
    token = secrets.token_urlsafe(16)
    SESSIONS[token] = CallSession(goal=goal, language=language)
    call = twilio_client(ctx.settings).calls.create(
        to=to,
        from_=ctx.settings.twilio_number,
        url=f"{base}/voice/agent/{token}",
        status_callback=f"{base}/voice/status/{token}",
        status_callback_event=["completed"],
    )
    SESSIONS[token].call_sid = call.sid
    return f"Calling {to} (call id {call.sid}). I'll report back when the call ends."


class PhoneBrain:
    """Generates Jarvis's next line in a phone conversation."""

    def __init__(self, ctx, client: anthropic.Anthropic | None = None):
        self.ctx = ctx
        self.client = client or anthropic.Anthropic()

    def system(self, sess: CallSession) -> str:
        user = self.ctx.settings.user_name
        role = (
            f"You answer phone calls for your owner ({user}) as their assistant Jarvis. Find out who is calling, "
            "why, and how to reach them; offer to pass on a message. Do not promise anything on the owner's behalf."
            if sess.inbound
            else f"You are Jarvis, an AI assistant phoning on behalf of your owner ({user}). Goal of this call: {sess.goal}"
        )
        return (
            f"{role}\nSpeak naturally in the call's language ({sess.language}); keep each turn to one or two short "
            "sentences, since it is read aloud by a phone voice. If asked, say honestly that you are an AI assistant. "
            "Share only details given in the goal. When the goal is reached, or the other side wants to end, say "
            "goodbye and set done=true with a short summary for the owner (in Bulgarian)."
        )

    def next(self, sess: CallSession) -> dict:
        messages = [{"role": "user", "content": "(The call has connected. Open the conversation.)"}]
        turns = list(sess.transcript)
        if turns and turns[-1]["who"] == "jarvis":
            turns.append({"who": "them", "text": "(silence)"})
        for turn in turns:
            role = "assistant" if turn["who"] == "jarvis" else "user"
            if messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + turn["text"]
            else:
                messages.append({"role": role, "content": turn["text"]})
        response = self.client.messages.create(
            model=self.ctx.settings.model,
            max_tokens=2000,
            system=self.system(sess),
            messages=messages,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
        )
        if response.stop_reason == "refusal":
            return {"say": "Извинете, ще трябва да затворя. Довиждане.", "done": True, "summary": "Разговорът беше прекъснат."}
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)


AUDIO: dict[str, bytes] = {}
NEURAL_VOICES = {"bg": "bg-BG-BorislavNeural", "en": "en-GB-RyanNeural", "de": "de-DE-ConradNeural",
                 "ru": "ru-RU-DmitryNeural", "fr": "fr-FR-HenriNeural", "es": "es-ES-AlvaroNeural",
                 "it": "it-IT-DiegoNeural", "tr": "tr-TR-AhmetNeural", "el": "el-GR-NestorasNeural"}


def neural_audio(text: str, language: str) -> str | None:
    """Render text with Jarvis's neural voice and return a public URL Twilio can <Play>, or None."""
    base = os.environ.get("JARVIS_PUBLIC_URL", "").rstrip("/")
    lang = language.split("-")[0].lower()
    voice = os.environ.get("JARVIS_TTS_VOICE") if lang == "bg" else None
    voice = voice or NEURAL_VOICES.get(lang)
    if not base or not voice or os.environ.get("JARVIS_PHONE_NEURAL", "1") != "1":
        return None
    try:
        import asyncio

        import edge_tts

        async def render() -> bytes:
            chunks = []
            async for part in edge_tts.Communicate(text, voice).stream():
                if part["type"] == "audio":
                    chunks.append(part["data"])
            return b"".join(chunks)

        data = asyncio.run(render())
    except Exception as exc:
        log.warning("neural phone voice unavailable (%s); using Twilio's voice", exc)
        return None
    key = secrets.token_urlsafe(16)
    AUDIO[key] = data
    while len(AUDIO) > 200:
        AUDIO.pop(next(iter(AUDIO)))
    return f"{base}/audio/{key}.mp3"


def speech_twiml(text: str, language: str) -> str:
    url = neural_audio(text, language)
    if url:
        return f"<Play>{escape(url)}</Play>"
    voice = os.environ.get("TWILIO_VOICE", "Google.bg-BG-Standard-A") if language.startswith("bg") else "Polly.Brian"
    return f'<Say voice="{escape(voice)}" language="{escape(language)}">{escape(text)}</Say>'


def _twiml_turn(sess: CallSession, reply: dict, action: str) -> str:
    say = speech_twiml(reply["say"], sess.language)
    if reply["done"]:
        return f"<Response>{say}<Hangup/></Response>"
    gather = (
        f'<Gather input="speech" language="{escape(sess.language)}" speechTimeout="auto" '
        f'action="{escape(action)}" method="POST">{say}</Gather>'
    )
    # If the caller says nothing, loop back with an empty result.
    return f'<Response>{gather}<Redirect method="POST">{escape(action)}</Redirect></Response>'


def handle(ctx, brain: PhoneBrain, path: str, params: dict[str, str]) -> str:
    parts = [p for p in path.split("/") if p]
    if parts[:2] == ["voice", "incoming"]:
        token = secrets.token_urlsafe(16)
        SESSIONS[token] = CallSession(goal="", language=os.environ.get("TWILIO_LANGUAGE", "bg-BG"), inbound=True,
                                      call_sid=params.get("CallSid"))
        SESSIONS[token].transcript.append({"who": "system", "text": f"Caller number: {params.get('From', 'unknown')}"})
        parts = ["voice", "agent", token]
    if len(parts) >= 3 and parts[1] == "status":
        sess = SESSIONS.get(parts[2])
        if sess and not sess.finished:
            sess.finished = True
            _report(ctx, sess, params.get("CallStatus", ""))
        return "<Response/>"
    if len(parts) >= 3 and parts[1] == "agent":
        token = parts[2]
        sess = SESSIONS.get(token)
        if sess is None:
            return "<Response><Hangup/></Response>"
        heard = params.get("SpeechResult")
        if heard:
            sess.silences = 0
            sess.transcript.append({"who": "them", "text": heard})
        elif sess.transcript and sess.transcript[-1]["who"] == "jarvis":
            sess.silences += 1
        if sess.silences >= 3:
            reply = {"say": "Не ви чувам. Довиждане.", "done": True, "summary": "Отсреща не отговориха."}
        else:
            reply = brain.next(sess)
        sess.transcript.append({"who": "jarvis", "text": reply["say"]})
        if reply["done"]:
            sess.summary = reply.get("summary", "")
            if sess.inbound and not sess.finished:  # inbound calls get no status callback from us
                sess.finished = True
                _report(ctx, sess, "completed")
        action = f"{os.environ.get('JARVIS_PUBLIC_URL', '').rstrip('/')}/voice/agent/{token}"
        return _twiml_turn(sess, reply, action)
    return "<Response/>"


def _report(ctx, sess: CallSession, status: str) -> None:
    kind = "Входящо обаждане" if sess.inbound else "Обаждане"
    lines = [f"{kind} приключи ({status})."]
    if sess.summary:
        lines.append(sess.summary)
    elif sess.transcript:
        lines.append("Разговор: " + " | ".join(f"{t['who']}: {t['text']}" for t in sess.transcript[-8:]))
    ctx.notify(" ".join(lines))


def ensure_server(ctx, port: int | None = None) -> ThreadingHTTPServer:
    """Start the webhook server once, in a background thread."""
    if "server" in _SERVER:
        return _SERVER["server"]
    port = port or int(os.environ.get("JARVIS_PHONE_PORT", "8765"))
    brain = PhoneBrain(ctx)
    validator = None
    if ctx.settings.twilio_token:
        from twilio.request_validator import RequestValidator

        validator = RequestValidator(ctx.settings.twilio_token)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            key = self.path.removeprefix("/audio/").removesuffix(".mp3")
            data = AUDIO.get(key) if self.path.startswith("/audio/") else None
            self.send_response(200 if data else 404)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data or b"")))
            self.end_headers()
            self.wfile.write(data or b"")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode()
            params = {k: v[0] for k, v in parse_qs(raw).items()}
            url = os.environ.get("JARVIS_PUBLIC_URL", "").rstrip("/") + self.path
            if validator and not validator.validate(url, params, self.headers.get("X-Twilio-Signature", "")):
                self.send_response(403)
                self.end_headers()
                return
            try:
                body = handle(ctx, brain, self.path, params)
            except Exception:
                log.exception("phone webhook failed")
                body = "<Response><Say>Sorry, an error occurred.</Say><Hangup/></Response>"
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="jarvis-phone", daemon=True).start()
    _SERVER["server"] = server
    log.info("phone webhook listening on :%s", port)
    return server
