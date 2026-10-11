"""`jarvis setup`: asks for each key in plain Bulgarian, writes .env, and can turn on autostart."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

STEPS = [
    ("Мозъкът: Claude (платен)", "console.anthropic.com > API Keys", [("ANTHROPIC_API_KEY", "Claude API ключ")]),
    ("Мозъкът: Gemini (безплатен)", "aistudio.google.com/apikey > Create API key", [("GEMINI_API_KEY", "Gemini API ключ")]),
    ("Бърз безплатен мозък: Groq", "console.groq.com/keys > Create API Key (без карта)", [("GROQ_API_KEY", "Groq API ключ")]),
    ("Безплатни силни модели: NVIDIA", "build.nvidia.com > Get API Key (без карта)", [("NVIDIA_API_KEY", "NVIDIA API ключ")]),
    ("Безплатни модели: OpenRouter", "openrouter.ai/keys > Create API Key", [("OPENROUTER_API_KEY", "OpenRouter API ключ")]),
    ("Как да се обръща към теб", "", [("JARVIS_USER_NAME", "Обръщение (напр. сър, шефе, Анастас)")]),
    ("Обаждания и SMS", "twilio.com > Console (купи номер)", [
        ("TWILIO_ACCOUNT_SID", "Account SID"), ("TWILIO_AUTH_TOKEN", "Auth Token"),
        ("TWILIO_PHONE_NUMBER", "Номерът на Jarvis (+1…)"), ("JARVIS_OWNER_PHONE", "Твоят мобилен (+359…)"),
        ("JARVIS_PUBLIC_URL", "Публичен адрес за живи разговори (напр. от ngrok), по желание"),
    ]),
    ("Плащания (приемане)", "dashboard.stripe.com > Developers > API keys", [("STRIPE_API_KEY", "Secret key")]),
    ("Плащания към други", "developer.paypal.com / wise.com > API tokens", [
        ("PAYPAL_CLIENT_ID", "PayPal Client ID"), ("PAYPAL_SECRET", "PayPal Secret"), ("WISE_API_TOKEN", "Wise API token"),
    ]),
    ("Имейл (за Gmail: App Password)", "", [
        ("JARVIS_SMTP_HOST", "SMTP сървър (smtp.gmail.com)"), ("JARVIS_IMAP_HOST", "IMAP сървър (imap.gmail.com)"),
        ("JARVIS_EMAIL_USER", "Имейл адрес"), ("JARVIS_EMAIL_PASSWORD", "Парола / App Password"),
    ]),
    ("Google Calendar и Gmail директно", "console.cloud.google.com > OAuth client (Desktop app)", [
        ("GOOGLE_CLIENT_SECRET", "Път до изтегления client_secret.json"),
    ]),
    ("Telegram", "@BotFather (токен) и @userinfobot (твоя id)", [
        ("TELEGRAM_BOT_TOKEN", "Токен на бота"), ("TELEGRAM_OWNER_ID", "Твоят Telegram id"),
    ]),
    ("Умен дом", "Home Assistant > Профил > Long-lived access tokens", [
        ("HOME_ASSISTANT_URL", "Адрес (http://homeassistant.local:8123)"), ("HOME_ASSISTANT_TOKEN", "Токен"),
    ]),
    ("Android телефон / TV", "Developer options > Wireless debugging", [
        ("JARVIS_ADB_DEVICES", "напр. phone=192.168.1.20:5555,tv=192.168.1.30:5555"),
    ]),
    ("WhatsApp през API (иначе през WhatsApp Web)", "developers.facebook.com > WhatsApp", [
        ("WHATSAPP_TOKEN", "Access token"), ("WHATSAPP_PHONE_ID", "Phone number ID"),
    ]),
    ("Известия на телефона (iPhone и Android)", "приложение ntfy, абонирай се за тема", [
        ("NTFY_TOPIC", "Име на тема (дълго и тайно, напр. jarvis-ab12cd34)"),
    ]),
]


def env_path() -> Path:
    """The .env Jarvis reads: the nearest one from the current folder up, else ./.env."""
    try:
        from dotenv import find_dotenv

        found = find_dotenv(usecwd=True)
    except ImportError:  # pragma: no cover
        found = ""
    return Path(found) if found else Path.cwd() / ".env"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # "a#b" stays, "a  # note" -> "a"
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = [f"{k}={v}" for k, v in values.items() if v]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def run() -> None:
    path = Path.cwd() / ".env"
    values = read_env(path)
    print("Настройка на J.A.R.V.I.S. Натисни Enter, за да пропуснеш или запазиш текущото.\n")
    for title, where, fields in STEPS:
        print(f"── {title}" + (f"  ({where})" if where else ""))
        for key, label in fields:
            current = values.get(key, "")
            shown = (current[:4] + "…") if current and ("KEY" in key or "TOKEN" in key or "SECRET" in key or "PASSWORD" in key) else current
            answer = input(f"   {label}" + (f" [{shown}]" if shown else "") + ": ").strip()
            if answer:
                values[key] = answer
        print()
    values.setdefault("JARVIS_WEB_TOKEN", secrets.token_urlsafe(24))
    write_env(path, values)
    print(f"Записах {path}.")
    print("Приложението: `jarvis app` (или иконата Jarvis на работния плот).")

    if values.get("GOOGLE_CLIENT_SECRET") and input("Да вляза ли в Google сега? [да/не]: ").strip().lower().startswith("д"):
        subprocess.run([sys.executable, "-m", "jarvis", "google-login"])
    if input("Да запиша ли гласа ти, за да слуша само теб? [да/не]: ").strip().lower().startswith("д"):
        subprocess.run([sys.executable, "-m", "jarvis", "enroll-voice"], check=False)
    if input("Да тръгва ли Jarvis сам при включване на компютъра? [да/не]: ").strip().lower().startswith("д"):
        from .autostart import install

        print(install(Path.cwd()))
    from .autostart import shortcut

    print(shortcut(Path.cwd()))
    if input("Да създам ли сега твоя акаунт за приложението (имейл и парола)? [да/не]: ").strip().lower().startswith("д"):
        subprocess.run([sys.executable, "-m", "jarvis", "owner"], check=False)
    print("\nГотово. Отвори Jarvis от иконата на работния плот или с `jarvis app`.")


if __name__ == "__main__":  # pragma: no cover
    os.chdir(Path(__file__).resolve().parent.parent)
    run()
