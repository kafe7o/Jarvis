"""Settings for Jarvis, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # .env support is optional
    from dotenv import load_dotenv

    load_dotenv()  # the nearest .env from the current folder up (the project folder when started from it)
    # Keys also live in ~/.jarvis/.env (where Settings and the app write them); read them too, whatever folder
    # Jarvis was started from, so a key added there counts at once. Values already set are not overridden.
    load_dotenv(Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis") / ".env")
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


def silence_seconds() -> float:
    """How long a pause ends what the owner says (JARVIS_SILENCE): Jarvis starts only after it, never while
    they are still talking. Read each time, so a change in Settings counts at once."""
    try:
        seconds = float((_env("JARVIS_SILENCE", "2") or "2").replace(",", "."))
    except ValueError:
        return 2.0
    return min(max(seconds, 0.5), 10.0)


def brain_setting(value: str | None) -> str:
    """JARVIS_MODEL: "local" (the best model on this computer) or "local:<name>". A cloud brain's name left in .env
    from before (Mistral, Gemini, "auto") means the local one: Jarvis thinks only on the laptop now."""
    value = (value or "").strip()
    return value if value.startswith("local:") and "@" not in value else "local"


@dataclass
class Settings:
    # Brain
    model: str = field(default_factory=lambda: brain_setting(_env("JARVIS_MODEL")))
    effort: str = field(default_factory=lambda: _env("JARVIS_EFFORT", "xhigh"))
    max_tokens: int = field(default_factory=lambda: int(_env("JARVIS_MAX_TOKENS", "16000")))
    heartbeat_minutes: int = field(default_factory=lambda: int(_env("JARVIS_HEARTBEAT_MINUTES", "60")))
    quiet_hours: str = field(default_factory=lambda: _env("JARVIS_QUIET_HOURS", "23-7"))
    max_tool_rounds: int = field(default_factory=lambda: int(_env("JARVIS_MAX_TOOL_ROUNDS", "80")))

    # Speed (see router.py): commands without AI and a quick lane for short questions; the model on this computer
    # (empty = the best one installed, see local.pick).
    router: bool = field(default_factory=lambda: _flag("JARVIS_ROUTER", True))
    # an answer to a request for action with no tool used gets one more look (brain.ACT_CHECK)
    act_check: bool = field(default_factory=lambda: _flag("JARVIS_ACT_CHECK", True))
    learn: bool = field(default_factory=lambda: _flag("JARVIS_LEARN", True))  # lessons from failed or corrected tasks
    local_model: str = field(default_factory=lambda: _env("JARVIS_LOCAL_MODEL", ""))

    # Identity
    user_name: str = field(default_factory=lambda: _env("JARVIS_USER_NAME", "сър"))
    language: str = field(default_factory=lambda: _env("JARVIS_LANGUAGE", "bg"))
    timezone: str = field(default_factory=lambda: _env("JARVIS_TIMEZONE", "Europe/Sofia"))

    # Storage
    home: Path = field(default_factory=lambda: Path(_env("JARVIS_HOME", str(Path.home() / ".jarvis"))))
    # The notes vault (plugins/vault.py): Markdown the owner can also open in Obsidian
    vault: Path = field(default_factory=lambda: Path(_env("JARVIS_VAULT", str(Path.home() / "Documents" / "Jarvis Vault"))).expanduser())

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
