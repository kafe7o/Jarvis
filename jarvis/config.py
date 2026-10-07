"""Settings for Jarvis, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # .env support is optional
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass
class Settings:
    # Brain
    model: str = field(default_factory=lambda: _env("JARVIS_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: _env("JARVIS_EFFORT", "medium"))
    max_tokens: int = field(default_factory=lambda: int(_env("JARVIS_MAX_TOKENS", "16000")))
    refusal_fallback: bool = field(default_factory=lambda: _flag("JARVIS_REFUSAL_FALLBACK", True))
    max_tool_rounds: int = field(default_factory=lambda: int(_env("JARVIS_MAX_TOOL_ROUNDS", "80")))

    # Identity
    user_name: str = field(default_factory=lambda: _env("JARVIS_USER_NAME", "сър"))
    language: str = field(default_factory=lambda: _env("JARVIS_LANGUAGE", "bg"))
    timezone: str = field(default_factory=lambda: _env("JARVIS_TIMEZONE", "Europe/Sofia"))

    # Storage
    home: Path = field(default_factory=lambda: Path(_env("JARVIS_HOME", str(Path.home() / ".jarvis"))))

    # Local actions (shell, file writes). Calls, SMS, e-mail and payments ALWAYS ask.
    trust_local_actions: bool = field(default_factory=lambda: _flag("JARVIS_TRUST_LOCAL", False))
    allowed_roots: list[str] = field(
        default_factory=lambda: [p for p in (_env("JARVIS_FILE_ROOTS", str(Path.home())) or "").split(os.pathsep) if p]
    )

    # Phone (Twilio)
    twilio_sid: str | None = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID"))
    twilio_token: str | None = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN"))
    twilio_number: str | None = field(default_factory=lambda: _env("TWILIO_PHONE_NUMBER"))
    owner_phone: str | None = field(default_factory=lambda: _env("JARVIS_OWNER_PHONE"))

    # Payments (Stripe)
    stripe_key: str | None = field(default_factory=lambda: _env("STRIPE_API_KEY"))
    currency: str = field(default_factory=lambda: _env("JARVIS_CURRENCY", "eur"))

    # E-mail
    smtp_host: str | None = field(default_factory=lambda: _env("JARVIS_SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("JARVIS_SMTP_PORT", "587")))
    imap_host: str | None = field(default_factory=lambda: _env("JARVIS_IMAP_HOST"))
    email_user: str | None = field(default_factory=lambda: _env("JARVIS_EMAIL_USER"))
    email_password: str | None = field(default_factory=lambda: _env("JARVIS_EMAIL_PASSWORD"))

    # Telegram (talk to Jarvis from your phone)
    telegram_token: str | None = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_owner_id: str | None = field(default_factory=lambda: _env("TELEGRAM_OWNER_ID"))

    # Voice
    wake_word: str = field(default_factory=lambda: _env("JARVIS_WAKE_WORD", "jarvis"))
    whisper_model: str = field(default_factory=lambda: _env("JARVIS_WHISPER_MODEL", "small"))
    tts_voice: str = field(default_factory=lambda: _env("JARVIS_TTS_VOICE", "bg-BG-BorislavNeural"))

    @property
    def db_path(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home / "jarvis.db"


settings = Settings()
