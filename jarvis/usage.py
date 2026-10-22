"""How much the brain worked: every answer's tokens are counted and summed up for the app's "Статистика" tab. The
brain runs on this computer, so it costs nothing."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

log = logging.getLogger("jarvis.usage")


def tokens_of(response, model: str) -> dict:
    """Token counts of an answer (see answers.Answer.usage)."""
    usage = getattr(response, "usage", None)
    counts = usage if isinstance(usage, dict) else {}
    return {"model": getattr(response, "model", None) or model, **counts}


def record(store, response, model: str) -> None:
    """Count one answer. Never lets counting break an answer."""
    try:
        t = tokens_of(response, model)
        store.insert("usage", model=str(t["model"]), input=t.get("input", 0), output=t.get("output", 0),
                     cache_read=t.get("cache_read", 0), cache_write=t.get("cache_write", 0))
    except Exception:
        log.exception("could not count usage")


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
    """Today's work in one sentence, for "колко похарчих днес" (answered without AI)."""
    levels = levels_today(store)
    return (f"Не харча нищо: мисля на лаптопа. Днес отговорих {times(levels['1'])} без AI, {times(levels['2'])} "
            f"с бързия модел и {times(levels['3'])} с пълния агент.")


def summary(store, days: int = 14) -> dict:
    """Requests and tokens today, per day and per model, and which level answered today."""
    today = datetime.now().date()
    since = (today - timedelta(days=days - 1)).isoformat()
    rows = store.query("SELECT model, input, output, created FROM usage WHERE created >= ?", (since,))
    per_day = {(today - timedelta(days=i)).isoformat(): {"requests": 0, "tokens": 0} for i in range(days)}
    models: dict[str, dict] = {}
    for r in rows:
        tokens = (r["input"] or 0) + (r["output"] or 0)
        day = per_day.get(r["created"][:10])
        if day is not None:
            day["requests"] += 1
            day["tokens"] += tokens
        m = models.setdefault(r["model"], {"model": r["model"], "requests": 0, "tokens": 0})
        m["requests"] += 1
        m["tokens"] += tokens
    week = [per_day[d] for d in sorted(per_day)[-7:]]
    return {
        "today": {**per_day[today.isoformat()]},
        "week": {"requests": sum(d["requests"] for d in week), "tokens": sum(d["tokens"] for d in week)},
        "days": [{"day": d, **per_day[d]} for d in sorted(per_day)],
        "models": sorted(models.values(), key=lambda m: -m["requests"]),
        "levels": levels_today(store),
    }
