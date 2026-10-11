"""„Намери клиенти“: local businesses without a website, and a ready demo site for each.

From a video of a Jarvis that finds businesses with no website, builds them one in seconds and writes the
pitch. find_leads looks them up for free in OpenStreetMap (no key): businesses of a kind near a city that
list no website and belong to no chain, with the phone and address they list. build_website fills a
polished one-page template (fast and cheap: the model only writes a few lines of text, never the HTML),
saves it in Documents/Jarvis Sites and opens it. The pitch is a draft; sending it needs the owner's yes.
"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

from ..tools import ToolRegistry, obj
from .daily import HEADERS, locate

OVERPASS = "https://overpass-api.de/api/interpreter"
# What people call a kind of business -> its OpenStreetMap tag, a look and an icon for its site.
KINDS = [
    (("фризьор", "бръснар", "barber", "hairdress"), "shop=hairdresser", "beauty", "✂️"),
    (("козмет", "маникюр", "салон за красота", "beauty", "spa"), "shop=beauty", "beauty", "💅"),
    (("ресторант", "restaurant", "механа", "таверна"), "amenity=restaurant", "food", "🍽️"),
    (("кафе", "cafe", "coffee"), "amenity=cafe", "food", "☕"),
    (("пекарн", "хлебар", "bakery"), "shop=bakery", "food", "🥐"),
    (("сладкарниц", "торти", "confection"), "shop=confectionery", "food", "🍰"),
    (("цвет", "florist"), "shop=florist", "beauty", "💐"),
    (("автосервиз", "сервиз", "автомонт", "car repair", "mechanic"), "shop=car_repair", "auto", "🔧"),
    (("гуми", "вулканиз", "tyre", "tire"), "shop=tyres", "auto", "🛞"),
    (("автомивк", "car wash"), "amenity=car_wash", "auto", "🚿"),
    (("зъболек", "стоматол", "дентал", "dentist"), "amenity=dentist", "health", "🦷"),
    (("ветеринар", "veterinar"), "amenity=veterinary", "health", "🐾"),
    (("водопровод", "вик", "plumber"), "craft=plumber", "craft", "🚰"),
    (("електротехн", "електричар", "electrician"), "craft=electrician", "craft", "💡"),
    (("строител", "ремонт", "builder"), "craft=builder", "craft", "🏗️"),
    (("дърводел", "мебел", "carpenter"), "craft=carpenter", "craft", "🪚"),
    (("шивач", "шивашк", "tailor"), "craft=tailor", "craft", "🧵"),
    (("обущар", "shoemaker"), "craft=shoemaker", "craft", "👞"),
    (("ключар", "locksmith"), "craft=locksmith", "craft", "🔑"),
    (("фитнес", "gym", "fitness"), "leisure=fitness_centre", "health", "🏋️"),
    (("къща за гости", "guest house"), "tourism=guest_house", "stay", "🏡"),
    (("хотел", "hotel"), "tourism=hotel", "stay", "🛏️"),
    (("фотограф", "photograph"), "craft=photographer", "craft", "📷"),
    (("счетовод", "accountant"), "office=accountant", "office", "📊"),
    (("адвокат", "lawyer"), "office=lawyer", "office", "⚖️"),
    (("имоти", "недвижим", "estate"), "office=estate_agent", "office", "🏠"),
    (("бижу", "jewel"), "shop=jewelry", "beauty", "💍"),
    (("дрехи", "бутик", "clothes"), "shop=clothes", "beauty", "👗"),
]
LOOKS = {  # accent, deep accent, soft background
    "beauty": ("#d6457a", "#8f1f4b", "#fff4f7"), "food": ("#e0702b", "#9a3f0c", "#fff7f0"),
    "auto": ("#2f6fe0", "#173f8f", "#f2f6ff"), "health": ("#13a594", "#0b6158", "#effbf9"),
    "craft": ("#d9822b", "#7a4510", "#fff8ef"), "stay": ("#8a5cf6", "#4c2a9e", "#f6f2ff"),
    "office": ("#3b4fd8", "#1f2a7a", "#f3f4ff"), "other": ("#5b5bd6", "#2f2f8a", "#f4f4ff"),
}
LATIN = dict(zip("абвгдежзийклмнопрстуфхцчшщъьюя",
                 "a b v g d e zh z i y k l m n o p r s t u f h ts ch sh sht a y yu ya".split()))


def kind_of(kind: str) -> tuple[str, str, str]:
    """(OpenStreetMap tag, look, icon) for what the owner said, e.g. "фризьори" -> shop=hairdresser."""
    said = kind.casefold().strip()
    if re.fullmatch(r"[a-z_:]+=[a-z_;]+", said):
        return said, "other", "⭐"
    for words, tag, look, icon in KINDS:
        if any(w in said for w in words):
            return tag, look, icon
    return "", "other", "⭐"


def slug(text: str) -> str:
    latin = "".join(LATIN.get(ch, ch) for ch in text.casefold())
    return re.sub(r"[^a-z0-9]+", "-", latin).strip("-")[:60] or "site"


def fetch_json(url: str, data: dict, timeout: float = 40) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def without_website(elements: list[dict]) -> list[dict]:
    """Named, independent businesses (no brand) that list no website, those with a phone first."""
    out = []
    for e in elements:
        tags = e.get("tags") or {}
        if not tags.get("name") or tags.get("brand") or tags.get("brand:wikidata"):
            continue
        if any(tags.get(k) for k in ("website", "contact:website", "url", "contact:url")):
            continue
        street = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
        center = e.get("center") or e
        out.append({
            "name": tags["name"], "phone": tags.get("phone") or tags.get("contact:phone") or "",
            "email": tags.get("email") or tags.get("contact:email") or "",
            "address": ", ".join(x for x in (street, tags.get("addr:city")) if x),
            "hours": tags.get("opening_hours", ""), "facebook": tags.get("contact:facebook") or tags.get("facebook") or "",
            "lat": center.get("lat"), "lon": center.get("lon"),
        })
    return sorted(out, key=lambda b: (not b["phone"], not b["address"], b["name"]))


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def render_site(info: dict) -> str:
    """The one-page site: hero, services, about, contacts with a map. Only what the owner's data says."""
    tag, look, icon = kind_of(info.get("kind", ""))
    accent, deep, soft = LOOKS[look]
    if re.fullmatch(r"#[0-9a-fA-F]{6}", info.get("accent") or ""):
        accent = info["accent"]
    name, city, phone = info["name"], info.get("city", ""), info.get("phone", "")
    tel = re.sub(r"[^\d+]", "", phone)
    services = [s for s in (info.get("services") or []) if str(s).strip()][:6]
    cards = "".join(
        f'<article class="card"><div class="ic">{icon}</div><h3>{esc(title.strip())}</h3>'
        + (f"<p>{esc(text.strip())}</p>" if text.strip() else "") + "</article>"
        for title, _, text in (str(s).partition(":") for s in services))
    address = info.get("address") or ""
    if address and city and city.casefold() not in address.casefold():
        address += f", {city}"
    rows = [("Адрес", esc(address)),
            ("Телефон", f'<a href="tel:{esc(tel)}">{esc(phone)}</a>' if phone else ""),
            ("Имейл", f'<a href="mailto:{esc(info.get("email"))}">{esc(info.get("email"))}</a>' if info.get("email") else ""),
            ("Работно време", esc(info.get("hours")))]
    contacts = "".join(f"<div><span>{k}</span><b>{v}</b></div>" for k, v in rows if v)
    lat, lon = info.get("lat"), info.get("lon")
    map_frame = ""
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        box = f"{lon - 0.006},{lat - 0.003},{lon + 0.006},{lat + 0.003}"
        map_frame = (f'<iframe class="map" title="Карта" loading="lazy" src="https://www.openstreetmap.org/export/embed.html'
                     f'?bbox={box}&amp;layer=mapnik&amp;marker={lat},{lon}"></iframe>')
    call = f'<a class="btn" href="tel:{esc(tel)}">Обадете се · {esc(phone)}</a>' if phone else ""
    small_call = f'<a class="btn small" href="tel:{esc(tel)}">Обадете се</a>' if phone else ""
    kicker = " · ".join(x for x in (info.get("kind", "").strip().capitalize(), city) if x)
    return f"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(name)}{f" · {esc(city)}" if city else ""}</title>
<meta name="description" content="{esc(info.get('tagline'))}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root {{ --a: {accent}; --d: {deep}; --soft: {soft}; --ink: #16181d; --mute: #5c6270; }}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin: 0; font-family: Manrope, system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--ink); background: #fff; line-height: 1.6; }}
a {{ color: inherit; }}
.wrap {{ max-width: 1120px; margin: 0 auto; padding: 0 24px; }}
nav {{ position: sticky; top: 0; z-index: 5; backdrop-filter: blur(12px); background: rgba(255,255,255,.82); border-bottom: 1px solid #eef0f4; }}
nav .wrap {{ display: flex; align-items: center; gap: 28px; height: 68px; }}
.logo {{ font-weight: 800; font-size: 20px; letter-spacing: -.02em; text-decoration: none; display: flex; gap: 10px; align-items: center; }}
.logo i {{ font-style: normal; width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; background: var(--a); color: #fff; font-size: 18px; }}
nav .links {{ margin-left: auto; display: flex; gap: 22px; font-weight: 600; font-size: 15px; }}
nav .links a {{ text-decoration: none; color: var(--mute); }} nav .links a:hover {{ color: var(--ink); }}
.btn {{ display: inline-flex; align-items: center; gap: 8px; padding: 14px 22px; border-radius: 999px; background: var(--a); color: #fff; font-weight: 700;
  text-decoration: none; box-shadow: 0 10px 30px -10px var(--a); transition: transform .15s; }}
.btn:hover {{ transform: translateY(-2px); }}
.btn.small {{ padding: 10px 18px; font-size: 14px; }}
.btn.ghost {{ background: transparent; color: var(--d); box-shadow: none; border: 2px solid color-mix(in srgb, var(--a) 35%, transparent); }}
header {{ position: relative; overflow: hidden; background: radial-gradient(1200px 500px at 85% -10%, color-mix(in srgb, var(--a) 28%, transparent), transparent),
  radial-gradient(700px 400px at -10% 110%, color-mix(in srgb, var(--a) 16%, transparent), transparent), var(--soft); }}
header .wrap {{ display: grid; grid-template-columns: 1.25fr .9fr; gap: 48px; align-items: center; padding-top: 96px; padding-bottom: 104px; }}
.kicker {{ display: inline-block; padding: 6px 14px; border-radius: 999px; background: #fff; color: var(--d); font-weight: 700; font-size: 13px; letter-spacing: .04em;
  text-transform: uppercase; box-shadow: 0 4px 14px -6px rgba(0,0,0,.15); }}
h1 {{ font-size: clamp(40px, 6vw, 68px); line-height: 1.04; letter-spacing: -.035em; margin: 22px 0 18px; }}
.lead {{ font-size: 20px; color: var(--mute); max-width: 34ch; margin: 0 0 32px; }}
.cta {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.badge {{ position: relative; aspect-ratio: 1; border-radius: 36px; background: linear-gradient(145deg, var(--a), var(--d)); display: grid; place-items: center;
  font-size: clamp(90px, 12vw, 150px); box-shadow: 0 40px 80px -30px var(--d); }}
.badge .note {{ position: absolute; left: -28px; bottom: 28px; background: #fff; border-radius: 18px; padding: 14px 18px; font-size: 14px; box-shadow: 0 20px 40px -18px rgba(0,0,0,.35);
  max-width: 240px; }}
.badge .note b {{ display: block; font-size: 15px; }} .badge .note span {{ color: var(--mute); }}
section {{ padding: 96px 0; }}
h2 {{ font-size: clamp(30px, 4vw, 42px); letter-spacing: -.03em; line-height: 1.1; margin: 0 0 14px; }}
.sub {{ color: var(--mute); font-size: 18px; margin: 0 0 44px; max-width: 56ch; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; }}
.card {{ padding: 28px; border-radius: 22px; background: #fff; border: 1px solid #eef0f4; box-shadow: 0 1px 0 rgba(0,0,0,.02); transition: transform .2s, box-shadow .2s; }}
.card:hover {{ transform: translateY(-4px); box-shadow: 0 24px 50px -28px rgba(0,0,0,.25); }}
.card .ic {{ width: 52px; height: 52px; border-radius: 16px; display: grid; place-items: center; background: var(--soft); font-size: 24px; margin-bottom: 18px; }}
.card h3 {{ margin: 0 0 6px; font-size: 19px; }} .card p {{ margin: 0; color: var(--mute); }}
.about {{ background: var(--soft); }}
.about .wrap {{ display: grid; grid-template-columns: .9fr 1.1fr; gap: 56px; align-items: center; }}
.about p {{ font-size: 18px; color: #333845; margin: 0 0 16px; }}
.quote {{ border-radius: 28px; padding: 40px; background: linear-gradient(145deg, var(--a), var(--d)); color: #fff; font-size: 26px; font-weight: 800; letter-spacing: -.02em; line-height: 1.25; }}
.contact .wrap {{ display: grid; grid-template-columns: 1fr 1.1fr; gap: 40px; align-items: stretch; }}
.info {{ display: grid; gap: 14px; align-content: start; }}
.info div {{ padding: 18px 22px; border-radius: 18px; border: 1px solid #eef0f4; }}
.info span {{ display: block; font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--mute); }}
.info b {{ font-size: 18px; }} .info a {{ color: var(--d); }}
.map {{ width: 100%; min-height: 340px; height: 100%; border: 0; border-radius: 24px; background: var(--soft); }}
footer {{ padding: 36px 0 48px; color: var(--mute); font-size: 14px; border-top: 1px solid #eef0f4; }}
footer .wrap {{ display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
.demo {{ position: fixed; left: 16px; bottom: 16px; padding: 8px 14px; border-radius: 999px; background: rgba(22,24,29,.86); color: #fff; font-size: 12px; font-weight: 600; }}
@media (max-width: 860px) {{
  nav .links {{ display: none; }}
  header .wrap, .about .wrap, .contact .wrap {{ grid-template-columns: 1fr; }}
  header .wrap {{ padding-top: 56px; padding-bottom: 64px; }}
  .badge {{ max-width: 340px; width: 100%; margin: 0 auto; }} .badge .note {{ left: 12px; }}
  section {{ padding: 64px 0; }}
}}
</style>
</head>
<body>
<nav><div class="wrap"><a class="logo" href="#"><i>{icon}</i>{esc(name)}</a>
<div class="links"><a href="#services">Услуги</a><a href="#about">За нас</a><a href="#contact">Контакти</a></div>
{small_call}</div></nav>
<header><div class="wrap"><div>
<span class="kicker">{esc(kicker)}</span>
<h1>{esc(name)}</h1>
<p class="lead">{esc(info.get("tagline"))}</p>
<div class="cta">{call}<a class="btn ghost" href="#contact">Как да ни намерите</a></div>
</div>
<div class="badge">{icon}{f'<div class="note"><b>{esc(info.get("hours"))}</b><span>Работно време</span></div>' if info.get("hours") else ""}</div>
</div></header>
{f'<section id="services"><div class="wrap"><h2>Услуги</h2><p class="sub">Какво правим за вас{f" в {esc(city)}" if city else ""}.</p><div class="grid">{cards}</div></div></section>' if cards else ""}
{f'<section id="about" class="about"><div class="wrap"><div class="quote">{esc(info.get("tagline"))}</div><div><h2>За нас</h2><p>{esc(info.get("about"))}</p></div></div></section>' if info.get("about") else ""}
<section id="contact" class="contact"><div class="wrap"><div><h2>Контакти</h2><p class="sub">Заповядайте или ни се обадете.</p><div class="info">{contacts}</div></div>{map_frame}</div></section>
<footer><div class="wrap"><span>© {datetime.now().year} {esc(name)}</span><span>{esc(address)}</span></div></footer>
<div class="demo">Демо версия</div>
</body>
</html>
"""


def register(registry: ToolRegistry, ctx) -> None:
    def sites() -> Path:
        return Path(ctx.settings.vault).expanduser().parent / "Jarvis Sites"

    @registry.tool(
        "„Намери клиенти“: local businesses of a kind near a city that have no website (free OpenStreetMap data), "
        "with phone, e-mail and address when listed. For the owner's lead hunt: then make a demo site for the best "
        "ones with build_website and write a short, friendly pitch for each (offer the site they can see now). Save "
        "pitches as drafts (gmail_draft when there is an e-mail) or put them in your answer; never send without the "
        "owner's yes. The data can be incomplete: give the Google Maps link so the owner can check first.",
        obj({"kind": ("string", "What kind of business, e.g. фризьори, автосервизи, ресторанти, or an OSM tag like shop=bakery"),
             "city": ("string", "City or town, e.g. Пловдив"),
             "count?": ("integer", "How many to return (default 10)"),
             "radius_km?": ("number", "Search radius around the city centre in km (default 8)")}),
    )
    def find_leads(kind: str, city: str, count: int = 10, radius_km: float = 8):
        tag, _look, _icon = kind_of(kind)
        place, lat, lon = locate(city)
        around = f"(around:{int(max(1, min(float(radius_km or 8), 30)) * 1000)},{lat},{lon})"
        if tag:
            key, _, value = tag.partition("=")
            wanted = f'nwr["{key}"="{value}"]{around};'
        else:  # a kind we have no tag for: businesses whose name says it
            word = re.sub(r"[^\w\s-]", "", kind).strip()[:40]
            wanted = f'nwr["name"~"{word}",i]["shop"]{around};nwr["name"~"{word}",i]["craft"]{around};'
        query = f"[out:json][timeout:30];({wanted});out center tags 300;"
        found = without_website(fetch_json(OVERPASS, {"data": query}).get("elements", []))
        count = max(1, min(int(count or 10), 30))
        if not found:
            return f"Не намерих „{kind}“ без сайт около {place}. Опитай с по-широк радиус или друг вид бизнес."
        for b in found[:count]:
            b["google_maps"] = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(f"{b['name']} {place}")
        return {"city": place, "kind": kind, "without_website": len(found), "leads": found[:count],
                "note": "From OpenStreetMap: check on Google Maps that they really have no site before pitching."}

    @registry.tool(
        "Make a polished one-page website for a business from a ready template in seconds (hero, services, about, "
        "contacts with a map) and open it in the browser; it is saved in Documents/Jarvis Sites. Write only short "
        "texts and only facts you know: no invented reviews, prices, awards or years.",
        obj({"name": ("string", "Business name"), "kind": ("string", "Kind of business, e.g. фризьорски салон"),
             "city": ("string", "City"), "tagline": ("string", "One catchy line, max 12 words, in the site's language"),
             "about?": ("string", "2-3 sentences about the business"),
             "services?": ("array", "3-6 services, each 'Name: one short sentence'"),
             "phone?": ("string", "Phone"), "email?": ("string", "E-mail"), "address?": ("string", "Street address"),
             "hours?": ("string", "Opening hours, e.g. Пн-Пт 9:00-19:00"),
             "lat?": ("number", "Latitude (from find_leads) for the map"), "lon?": ("number", "Longitude"),
             "accent?": ("string", "Main colour as #rrggbb (default: one that suits the kind)"),
             "open?": ("boolean", "Open it in the browser (default true)")}),
    )
    def build_website(name: str, kind: str, city: str, tagline: str, about: str = "", services: list | None = None,
                      phone: str = "", email: str = "", address: str = "", hours: str = "", lat: float | None = None,
                      lon: float | None = None, accent: str = "", open: bool = True):
        info = {"name": name.strip(), "kind": kind, "city": city, "tagline": tagline, "about": about,
                "services": services or [], "phone": phone, "email": email, "address": address, "hours": hours,
                "lat": lat, "lon": lon, "accent": accent}
        folder = sites() / slug(f"{name} {city}")
        folder.mkdir(parents=True, exist_ok=True)
        page = folder / "index.html"
        page.write_text(render_site(info), encoding="utf-8")
        if open:
            webbrowser.open(page.as_uri())
        return (f"Сайтът е готов: {page}. За да го видят и други, качи папката безплатно в Netlify Drop "
                f"(app.netlify.com/drop): плъзгаш папката „{folder.name}“ и получаваш линк.")
