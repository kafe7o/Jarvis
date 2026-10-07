"""Accounts for the Jarvis app: logins, sessions and what each account may let Jarvis do.

People sign in with e-mail and password; nobody can sign up. The first account is the owner, made
on this computer the first time the app opens (or with `jarvis owner`). Only the owner creates
accounts, sets their e-mails and passwords and decides, per
group of abilities, whether Jarvis may use it for them: "on", "ask" (confirm every action) or "off".
Calls, messages and payments still ask before every action, whatever the setting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field

from .store import Store, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
    pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member', perms TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL, created TEXT NOT NULL,
    updated TEXT NOT NULL
);
"""

# (key, label, what it allows, sensitive)
GROUPS = [
    ("web", "Интернет", "Търсене в реално време и четене на страници", False),
    ("memory", "Памет и контакти", "Помни факти, търси в старите разговори, указател с хора", False),
    ("tasks", "Задачи и календар", "Задачи, напомняния, събития, задачи по график", False),
    ("system", "Компютър", "Файлове, команди, код, екран, мишка и клавиатура", True),
    ("browser", "Браузър", "Отваря сайтове, влиза в акаунти, попълва форми", True),
    ("android", "Android телефон и TV", "Звъни и праща SMS от телефона, управлява телевизора", True),
    ("home", "Умен дом", "Лампи, климатици, камери, ключалки и аларма", True),
    ("comms", "Обаждания, SMS и имейл", "Twilio обаждания и SMS, изпращане и четене на поща", True),
    ("messaging", "WhatsApp, Viber, известия", "Съобщения до хора и известия на телефона", True),
    ("google", "Google Calendar и Gmail", "Календар, покани, търсене и изпращане на поща", True),
    ("payments", "Плащания (Stripe)", "Линкове за плащане, фактури, възстановявания", True),
    ("sendmoney", "Пращане на пари", "PayPal и банкови преводи през Wise", True),
    ("agent", "Умения", "Jarvis сам си пише и пуска нови умения", True),
    ("team", "Екип от агенти", "Jarvis раздава работа на специалисти, които работят едновременно", False),
    ("devices", "Други устройства", "Лаптопи и компютри, свързани с jarvis node", True),
]
GROUP_KEYS = [g[0] for g in GROUPS]
MODES = ("on", "ask", "off")
OWNER_DEFAULT = {key: "on" for key in GROUP_KEYS}
MEMBER_DEFAULT = {key: ("on" if key == "web" else "off") for key in GROUP_KEYS}

SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 120:
        raise ValueError("Въведи истински имейл адрес.")
    return email


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _algo, salt, _digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class User:
    id: int
    username: str
    name: str
    role: str = "member"
    perms: dict = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def mode(self, group: str) -> str:
        base = OWNER_DEFAULT if self.is_owner else MEMBER_DEFAULT
        mode = self.perms.get(group, base.get(group, "off"))
        return mode if mode in MODES else "off"

    def allowed_groups(self) -> set[str]:
        return {g for g in GROUP_KEYS if self.mode(g) != "off"} | {"core"}

    def ask_groups(self) -> set[str]:
        return {g for g in GROUP_KEYS if self.mode(g) == "ask"}

    def public(self) -> dict:
        return {"id": self.id, "email": self.username, "username": self.username, "name": self.name, "role": self.role,
                "perms": {g: self.mode(g) for g in GROUP_KEYS}}


# Bearer-token clients (Siri shortcut, scripts, `jarvis node`) act as the owner.
TOKEN_USER = User(0, "token", "Собственик", "owner")


class PasswordError(ValueError):
    pass


class Accounts:
    def __init__(self, store: Store):
        self.store = store
        with store._lock:
            store.db.executescript(SCHEMA)
            store.db.commit()

    # users
    def _user(self, row: dict | None) -> User | None:
        if not row:
            return None
        return User(row["id"], row["username"], row["name"], row["role"], json.loads(row["perms"] or "{}"))

    def count(self) -> int:
        return self.store.query("SELECT COUNT(*) AS n FROM users")[0]["n"]

    def get(self, user_id: int) -> User | None:
        return self._user(next(iter(self.store.query("SELECT * FROM users WHERE id=?", (user_id,))), None))

    def pw_hash(self, user_id: int) -> str:
        rows = self.store.query("SELECT pw_hash FROM users WHERE id=?", (user_id,))
        return rows[0]["pw_hash"] if rows else ""

    def list(self) -> list[User]:
        return [self._user(r) for r in self.store.query("SELECT * FROM users ORDER BY id")]

    def owner(self) -> User | None:
        return self._user(next(iter(self.store.query("SELECT * FROM users WHERE role='owner' ORDER BY id")), None))

    def create(self, email: str, name: str, password: str, role: str = "member") -> User:
        """``email`` is the login (stored in the ``username`` column)."""
        email = clean_email(email)
        if len(password) < 6:
            raise PasswordError("Паролата трябва да е поне 6 знака.")
        if self.store.query("SELECT 1 FROM users WHERE username=?", (email,)):
            raise ValueError("Вече има акаунт с този имейл.")
        role = "owner" if role == "owner" else "member"
        uid = self.store.insert("users", username=email, name=name.strip() or email.split("@")[0],
                                pw_hash=hash_password(password), role=role, perms="{}")
        return self.get(uid)

    def update(self, user_id: int, *, name: str | None = None, password: str | None = None,
               role: str | None = None, perms: dict | None = None, email: str | None = None) -> User:
        user = self.get(user_id)
        if user is None:
            raise KeyError("Няма такъв акаунт.")
        if email is not None and email.strip() and email.strip().lower() != user.username:
            email = clean_email(email)
            if self.store.query("SELECT 1 FROM users WHERE username=? AND id<>?", (email, user_id)):
                raise ValueError("Вече има акаунт с този имейл.")
            self.store.execute("UPDATE users SET username=? WHERE id=?", (email, user_id))
        if name is not None and name.strip():
            self.store.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), user_id))
        if password is not None:
            if len(password) < 6:
                raise PasswordError("Паролата трябва да е поне 6 знака.")
            self.store.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(password), user_id))
            self.store.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        if role is not None and role in ("owner", "member") and role != user.role:
            if user.is_owner and len([u for u in self.list() if u.is_owner]) == 1:
                raise ValueError("Трябва да остане поне един собственик.")
            self.store.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
        if perms is not None:
            merged = dict(user.perms)
            merged.update({k: v for k, v in perms.items() if k in GROUP_KEYS and v in MODES})
            self.store.execute("UPDATE users SET perms=? WHERE id=?", (json.dumps(merged), user_id))
        return self.get(user_id)

    def set_owner(self, email: str, password: str, name: str = "") -> User:
        """Create the owner, or reset the first owner's e-mail and password (`jarvis owner`)."""
        owner = self.owner()
        if owner is None:
            return self.create(email, name, password, role="owner")
        return self.update(owner.id, email=email, password=password, name=name or None)

    def delete(self, user_id: int) -> None:
        user = self.get(user_id)
        if user is None:
            return
        if user.is_owner and len([u for u in self.list() if u.is_owner]) == 1:
            raise ValueError("Не може да изтриеш единствения собственик.")
        for chat in self.chats(user_id):
            self.delete_chat(user_id, chat["id"])
        self.store.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.store.execute("DELETE FROM users WHERE id=?", (user_id,))

    # sessions
    def login(self, email: str, password: str) -> tuple[User, str] | None:
        row = next(iter(self.store.query("SELECT * FROM users WHERE username=?", ((email or "").strip(),))), None)
        if not row or not check_password(password, row["pw_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        self.store.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        self.store.insert("sessions", token_hash=_token_hash(token), user_id=row["id"],
                          expires=time.time() + SESSION_DAYS * 86400)
        return self._user(row), token

    def session_user(self, token: str) -> User | None:
        if not token:
            return None
        row = next(iter(self.store.query("SELECT user_id, expires FROM sessions WHERE token_hash=?",
                                         (_token_hash(token),))), None)
        if not row or row["expires"] < time.time():
            return None
        return self.get(row["user_id"])

    def logout(self, token: str) -> None:
        self.store.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    # chats
    def chats(self, user_id: int) -> list[dict]:
        return self.store.query("SELECT id, title, updated FROM chats WHERE user_id=? ORDER BY updated DESC, id DESC",
                                (user_id,))

    def chat(self, user_id: int, chat_id: int) -> dict | None:
        return next(iter(self.store.query("SELECT * FROM chats WHERE id=? AND user_id=?", (chat_id, user_id))), None)

    def create_chat(self, user_id: int, title: str = "Нов разговор") -> dict:
        now = now_iso()
        cid = self.store.insert("chats", user_id=user_id, title=title[:80] or "Нов разговор", updated=now)
        return self.chat(user_id, cid)

    def rename_chat(self, user_id: int, chat_id: int, title: str) -> None:
        self.store.execute("UPDATE chats SET title=? WHERE id=? AND user_id=?", (title.strip()[:80], chat_id, user_id))

    def touch_chat(self, chat_id: int) -> None:
        self.store.execute("UPDATE chats SET updated=? WHERE id=?", (now_iso(), chat_id))

    def delete_chat(self, user_id: int, chat_id: int) -> None:
        if self.chat(user_id, chat_id):
            self.store.clear_history(conversation_id(chat_id))
            self.store.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    def messages(self, chat_id: int) -> list[dict]:
        return self.store.query("SELECT role, content, created FROM messages WHERE conversation=? ORDER BY id",
                                (conversation_id(chat_id),))


def conversation_id(chat_id: int) -> str:
    return f"chat:{chat_id}"
