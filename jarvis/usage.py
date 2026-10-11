"""What the brain costs: every answer's tokens are counted, priced and summed up for the app's "Разход" tab."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("jarvis.usage")

# US dollars per million tokens: input, output, cache read, cache write (5-minute cache).
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def free(model: str) -> bool:
    """The free tiers (Gemini, Groq, NVIDIA, OpenRouter) and the brain on this computer cost nothing."""
    return model.startswith(("gemini", "groq", "local", "nvidia", "openrouter"))


def price(model: str) -> tuple[float, float, float, float]:
    """Free brains cost nothing; an unknown Claude model is priced like Opus, so the sum is never too low."""
    if free(model):
        return 0.0, 0.0, 0.0, 0.0
    return PRICES.get(model) or next((p for name, p in PRICES.items() if model.startswith(name)), PRICES["claude-opus-5-5"])


def cost(model: str, input: int = 0, output: int = 0, cache_read: int = 0, cache_write: int = 0) -> float:
    p = price(model)
    return (input * p[0] + output * p[1] + cache_read * p[2] + cache_write * p[3]) / 1_000_000


def tokens_of(response, model: str) -> dict:
    """Token counts of a Claude or Gemini answer (see gemini.Answer.usage)."""
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):  # Gemini, already counted by gemini.from_response
        return {"model": getattr(response, "model", None) or model, **usage}
    get = (lambda k: int(getattr(usage, k, 0) or 0)) if usage is not None else (lambda k: 0)
    return {"model": getattr(response, "model", None) or model, "input": get("input_tokens"),
            "output": get("output_tokens"), "cache_read": get("cache_read_input_tokens"),
            "cache_write": get("cache_creation_input_tokens")}


def record(store, response, model: str) -> None:
    """Count one answer. Never lets counting break an answer."""
    try:
        t = tokens_of(response, model)
        store.insert("usage", model=str(t["model"]), input=t.get("input", 0), output=t.get("output", 0),
                     cache_read=t.get("cache_read", 0), cache_write=t.get("cache_write", 0))
    except Exception:
        log.exception("could not count usage")


def spent_today(store) -> float:
    """US dollars spent on paid brains today (for the daily cap, JARVIS_DAILY_BUDGET)."""
    rows = store.query("SELECT model, input, output, cache_read, cache_write FROM usage WHERE created >= ?",
                       (datetime.now().date().isoformat(),))
    return sum(cost(r["model"], r["input"], r["output"], r["cache_read"], r["cache_write"]) for r in rows)


def levels_today(store) -> dict[str, int]:
    """How many answers each level gave today: 1 = without AI, 2 = quick, 3 = the full agent (router.py)."""
    rows = store.query("SELECT tier, COUNT(*) AS n FROM routes WHERE created >= ? GROUP BY tier",
                       (datetime.now().date().isoformat(),))
    counts = {"1": 0, "2": 0, "3": 0}
    counts.update({str(r["tier"]): r["n"] for r in rows})
    return counts


def times(n: int) -> str:
    return f"{n} път" if n == 1 else f"{n} пъти"


def spoken(store) -> str:
    """Today's spending in one sentence, for "колко похарчих днес" (answered without AI)."""
    spent = spent_today(store)
    levels = levels_today(store)
    money = ("Днес не съм похарчил нищо" if spent < 0.005 else f"Днес похарчих {spent:.2f} долара".replace(".", ","))
    return (f"{money}. Отговорих {times(levels['1'])} без AI, {times(levels['2'])} с бързия модел и "
            f"{times(levels['3'])} с пълния агент.")


def free_day_start(now: datetime | None = None) -> datetime:
    """Google's free daily requests start again at midnight Pacific time (about 07:00 UTC)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    return start if start <= now else start - timedelta(days=1)


def summary(store, spent: dict | None = None, days: int = 14) -> dict:
    """Spending today, per day and per model, and how the free Gemini models stand today."""
    from .gemini import FREE_MODELS

    today = datetime.now().date()
    since = (today - timedelta(days=days - 1)).isoformat()
    rows = store.query("SELECT model, input, output, cache_read, cache_write, created FROM usage WHERE created >= ?", (since,))
    per_day = {(today - timedelta(days=i)).isoformat(): {"cost": 0.0, "requests": 0} for i in range(days)}
    models: dict[str, dict] = {}
    for r in rows:
        c = cost(r["model"], r["input"], r["output"], r["cache_read"], r["cache_write"])
        day = per_day.get(r["created"][:10])
        if day is not None:
            day["cost"] += c
            day["requests"] += 1
        m = models.setdefault(r["model"], {"model": r["model"], "requests": 0, "tokens": 0, "cost": 0.0, "free": free(r["model"])})
        m["requests"] += 1
        m["tokens"] += r["input"] + r["output"] + r["cache_read"] + r["cache_write"]
        m["cost"] += c
    # Free requests are counted from Google's reset, in this computer's local time as stored.
    reset = free_day_start().astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    used = {r["model"]: r["n"] for r in store.query(
        "SELECT model, COUNT(*) AS n FROM usage WHERE model LIKE 'gemini%' AND created >= ? GROUP BY model", (reset,))}
    now = time.time()
    back = lambda m: (spent or {}).get(m, 0)  # noqa: E731
    gemini = [{"model": m, "today": used.get(m, 0), "used_up": back(m) > now,
               "back_at": datetime.fromtimestamp(back(m)).strftime("%H:%M") if back(m) > now else None}
              for m in dict.fromkeys([*FREE_MODELS, *used])]
    week = [per_day[d] for d in sorted(per_day)[-7:]]
    return {
        "today": {**per_day[today.isoformat()]},
        "week": {"cost": sum(d["cost"] for d in week), "requests": sum(d["requests"] for d in week)},
        "days": [{"day": d, **per_day[d]} for d in sorted(per_day)],
        "models": sorted(models.values(), key=lambda m: -m["cost"] or -m["requests"]),
        "gemini": gemini,
        "levels": levels_today(store),
    }
