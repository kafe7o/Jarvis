"""Talk to Jarvis from your phone through a private Telegram bot (text and voice messages).

Only the owner's Telegram user id (TELEGRAM_OWNER_ID) is served; everyone else is ignored.
"""

from __future__ import annotations

import json
import logging
import queue
import tempfile
import threading
import urllib.parse
import urllib.request

from ..app import build, start_background
from ..config import settings
from ..plugins import NotConfigured
from ..confirm import is_yes

log = logging.getLogger("jarvis.telegram")


class Bot:
    def __init__(self, token: str, owner_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.owner_id = str(owner_id)
        self.offset = 0

    def call(self, method: str, timeout: float = 70, **params) -> dict:
        data = urllib.parse.urlencode({k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()}).encode()
        with urllib.request.urlopen(f"{self.base}/{method}", data=data, timeout=timeout) as resp:
            return json.loads(resp.read())["result"]

    def send(self, text: str, **extra) -> None:
        for start in range(0, len(text), 4000) or [0]:
            self.call("sendMessage", chat_id=self.owner_id, text=text[start:start + 4000] or "…", **extra)

    def updates(self):
        while True:
            try:
                batch = self.call("getUpdates", offset=self.offset, timeout=60)
            except Exception as exc:
                log.warning("getUpdates failed: %s", exc)
                continue
            for upd in batch:
                self.offset = upd["update_id"] + 1
                yield upd

    def download(self, file_id: str) -> str:
        info = self.call("getFile", file_id=file_id)
        url = self.base.replace("/bot", "/file/bot") + "/" + info["file_path"]
        fd, path = tempfile.mkstemp(suffix=".ogg")
        with urllib.request.urlopen(url, timeout=60) as resp, open(fd, "wb") as fh:
            fh.write(resp.read())
        return path


def run(shared=None) -> None:
    """shared: (jarvis, ctx) when several interfaces run in one process (jarvis serve)."""
    if not (settings.telegram_token and settings.telegram_owner_id):
        raise NotConfigured("Telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID"])
    bot = Bot(settings.telegram_token, settings.telegram_owner_id)
    answers: queue.Queue[str] = queue.Queue()
    waiting = threading.Event()
    busy = threading.Lock()
    transcriber = None

    def confirm(summary: str) -> bool:
        while not answers.empty():
            answers.get_nowait()
        waiting.set()
        bot.send(f"⚠ Да направя ли това?\n{summary}", reply_markup={"keyboard": [["Да", "Не"]], "one_time_keyboard": True, "resize_keyboard": True})
        try:
            return is_yes(answers.get(timeout=600))
        except queue.Empty:
            bot.send("Нямаше отговор, отказах действието.")
            return False
        finally:
            waiting.clear()

    if shared:
        jarvis, ctx = shared
        ctx.notifiers.append(lambda t: bot.send(f"🔔 {t}"))
    else:
        jarvis, ctx = build(confirm, [lambda t: bot.send(f"🔔 {t}")])
        start_background(ctx)
    bot.send("J.A.R.V.I.S. е на линия.", reply_markup={"remove_keyboard": True})

    def handle(text: str) -> None:
        with busy:
            try:
                bot.send(jarvis.ask(text, conversation="telegram", confirmer=confirm), reply_markup={"remove_keyboard": True})
            except Exception as exc:
                log.exception("request failed")
                bot.send(f"Грешка: {exc}")

    for upd in bot.updates():
        msg = upd.get("message") or {}
        if str((msg.get("from") or {}).get("id")) != bot.owner_id:
            continue
        text = msg.get("text") or ""
        if msg.get("voice"):
            try:
                if transcriber is None:
                    from ..voice.speech import Transcriber

                    transcriber = Transcriber(settings.whisper_model, settings.language)
                text = transcriber.file(bot.download(msg["voice"]["file_id"]))
                bot.send(f"🎙 {text}")
            except Exception as exc:
                bot.send(f"Не успях да разпозная гласовото съобщение: {exc}")
                continue
        if not text:
            continue
        if text in {"/new", "/нов"}:
            ctx.store.clear_history("telegram")
            bot.send("Започваме на чисто.")
        elif waiting.is_set():
            answers.put(text)
        else:
            threading.Thread(target=handle, args=(text,), daemon=True).start()
