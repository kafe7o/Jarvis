"""Everyday things Jarvis does on the spot: the weather, and music or videos from YouTube."""

from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
import urllib.request
import webbrowser

from ..tools import ToolRegistry, obj

WEATHER = {
    0: "ясно", 1: "предимно ясно", 2: "разкъсана облачност", 3: "облачно", 45: "мъгла", 48: "мъгла и скреж",
    51: "слаб ръмеж", 53: "ръмеж", 55: "силен ръмеж", 56: "леден ръмеж", 57: "леден ръмеж",
    61: "слаб дъжд", 63: "дъжд", 65: "силен дъжд", 66: "леден дъжд", 67: "леден дъжд",
    71: "слаб сняг", 73: "сняг", 75: "силен сняг", 77: "снежни зърна",
    80: "слаби превалявания", 81: "превалявания", 82: "силни превалявания", 85: "снеговалеж", 86: "силен снеговалеж",
    95: "гръмотевична буря", 96: "буря с градушка", 99: "буря с градушка",
}
DAYS = ["Днес", "Утре", "Вдругиден"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis", "Accept-Language": "bg,en;q=0.8"}


def fetch(url: str, timeout: float = 10, cookie: str | None = None) -> str:
    headers = dict(HEADERS, **({"Cookie": cookie} if cookie else {}))
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


_here: dict[str, tuple[str, float, float]] = {}  # where this computer is, looked up once


def locate(place: str | None, timeout: float = 10) -> tuple[str, float, float]:
    """(name, latitude, longitude) of a place; without one, the city from JARVIS_CITY or this internet connection."""
    place = (place or os.environ.get("JARVIS_CITY") or "").strip()
    if not place and "ip" in _here:
        return _here["ip"]
    if not place:
        try:
            here = json.loads(fetch("https://ipapi.co/json/", timeout=min(timeout, 5)))
            _here["ip"] = here["city"], float(here["latitude"]), float(here["longitude"])
            return _here["ip"]
        except Exception:
            place = "София"
    query = urllib.parse.urlencode({"name": place, "count": 1, "language": "bg", "format": "json"})
    found = json.loads(fetch(f"https://geocoding-api.open-meteo.com/v1/search?{query}", timeout)).get("results")
    if not found:
        raise ValueError(f"Не намерих място „{place}“.")
    return found[0]["name"], found[0]["latitude"], found[0]["longitude"]


def weather_report(place: str | None = None, days: int = 3, timeout: float = 10) -> str:
    """The weather now and for the next days, in Bulgarian (free Open-Meteo, no key)."""
    name, lat, lon = locate(place, timeout)
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": max(1, min(days, 7)),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    })
    data = json.loads(fetch(f"https://api.open-meteo.com/v1/forecast?{query}", timeout))
    now, daily = data["current"], data["daily"]
    lines = [f"{name}: сега {round(now['temperature_2m'])}°C (усеща се като {round(now['apparent_temperature'])}°C), "
             f"{WEATHER.get(now['weather_code'], 'променливо')}, вятър {round(now['wind_speed_10m'])} км/ч, "
             f"влажност {now['relative_humidity_2m']}%."]
    for i, day in enumerate(daily["time"]):
        label = DAYS[i] if i < len(DAYS) else day
        rain = daily["precipitation_probability_max"][i]
        lines.append(f"{label}: {round(daily['temperature_2m_min'][i])}…{round(daily['temperature_2m_max'][i])}°C, "
                     f"{WEATHER.get(daily['weather_code'][i], 'променливо')}"
                     + (f", вероятност за валеж {rain}%" if rain is not None else "") + ".")
    return "\n".join(lines)


def short_weather(timeout: float = 4) -> str:
    """One sentence for a greeting, or "" when the weather can't be read quickly."""
    try:
        name, lat, lon = locate(None, timeout)
        query = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code"})
        now = json.loads(fetch(f"https://api.open-meteo.com/v1/forecast?{query}", timeout))["current"]
        return f"Навън в {name} е {round(now['temperature_2m'])} градуса, {WEATHER.get(now['weather_code'], 'променливо')}."
    except Exception:
        return ""


def find_video(query: str) -> tuple[str, str] | None:
    """(video id, title) of YouTube's first result, without an API key."""
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query, "hl": "bg"})
    page = fetch(url, cookie="SOCS=CAI; CONSENT=YES+cb")  # skips the EU cookie page
    found = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page) or re.search(r'"videoId":"([\w-]{11})"', page)
    if not found:
        return None
    title = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', page[found.end():found.end() + 4000])
    return found[1], json.loads(f'"{title[1]}"') if title else query


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "The weather now and for the next days (free, no key). Without a place: the owner's city "
        "(JARVIS_CITY, else where this computer is).",
        obj({"place?": ("string", "City or place, e.g. 'Пловдив'"), "days?": ("integer", "Days of forecast, 1-7 (default 3)")}),
    )
    def weather(place: str | None = None, days: int = 3):
        return weather_report(place, days)

    @registry.tool(
        "Play a song, music or video from YouTube right away: finds the best match and starts it in the browser "
        "on this computer, or in the YouTube app on the phone or TV (device).",
        obj({"query": ("string", "What to play, e.g. 'AC/DC Back in Black' or 'лофи музика за работа'"),
             "device?": ("string", "Android device name (e.g. 'phone' or 'tv'); empty = this computer")}),
    )
    def play_youtube(query: str, device: str | None = None):
        try:
            found = find_video(query)
        except Exception:
            found = None
        if found:
            url, said = f"https://www.youtube.com/watch?v={found[0]}", f"Playing “{found[1]}”"
        else:  # YouTube's page changed or is unreachable: show the results instead
            url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})
            said = "Opened the YouTube results (could not pick the first video)"
        if device:
            from .android import adb, devices_from_env

            serial = devices_from_env().get(device)
            if not serial:
                raise ValueError(f"Unknown device '{device}'.")
            adb(serial, "shell", f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}")
            return f"{said} on the {device}: {url}"
        webbrowser.open(url)
        return f"{said}: {url}"
