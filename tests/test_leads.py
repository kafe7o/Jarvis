from __future__ import annotations

from jarvis.plugins import leads


def test_what_people_say_maps_to_a_kind_of_business():
    assert leads.kind_of("фризьори")[0] == "shop=hairdresser"
    assert leads.kind_of("Автосервизи")[0] == "shop=car_repair"
    assert leads.kind_of("зъболекари")[:2] == ("amenity=dentist", "health")
    assert leads.kind_of("shop=bakery")[0] == "shop=bakery"
    assert leads.kind_of("магазин за чай")[0] == ""
    assert leads.slug("Салон „Мария“ Пловдив") == "salon-mariya-plovdiv"


def place(name, **tags):
    return {"type": "node", "lat": 42.15, "lon": 24.75, "tags": {"name": name, **tags} if name else tags}


def test_find_leads_keeps_independent_businesses_without_a_site(registry, monkeypatch):
    asked = []
    monkeypatch.setattr(leads, "locate", lambda city: ("Пловдив", 42.14, 24.75))
    monkeypatch.setattr(leads, "fetch_json", lambda url, data: asked.append(data["data"]) or {"elements": [
        place("Салон Ана", website="https://ana.bg", phone="+359 88 111"),
        place("Chain Cuts", brand="Chain Cuts", phone="+359 88 222"),
        place("", phone="+359 88 333"),
        place("Бръснарница Иван", **{"addr:street": "ул. Гладстон", "addr:housenumber": "5"}),
        {"type": "way", "center": {"lat": 42.1, "lon": 24.7},
         "tags": {"name": "Студио Мария", "contact:phone": "+359 88 444", "opening_hours": "Mo-Fr 09:00-19:00"}},
    ]})
    out, is_error = registry.run("find_leads", {"kind": "фризьори", "city": "Пловдив", "radius_km": 5}, lambda s: True)
    assert not is_error
    assert 'nwr["shop"="hairdresser"](around:5000,42.14,24.75)' in asked[0]
    import json

    found = json.loads(out)
    assert [b["name"] for b in found["leads"]] == ["Студио Мария", "Бръснарница Иван"]  # with a phone first
    assert found["leads"][0]["phone"] == "+359 88 444" and found["leads"][0]["lat"] == 42.1
    assert found["leads"][1]["address"] == "ул. Гладстон 5"
    assert found["leads"][1]["google_maps"].endswith("%D0%9F%D0%BB%D0%BE%D0%B2%D0%B4%D0%B8%D0%B2")


def test_a_demo_site_is_built_from_the_template_and_opened(registry, settings, monkeypatch):
    opened = []
    monkeypatch.setattr(leads.webbrowser, "open", opened.append)
    out, is_error = registry.run("build_website", {
        "name": "Студио <Мария>", "kind": "фризьорски салон", "city": "Пловдив",
        "tagline": "Прическа, с която се чувстваш себе си.", "about": "Малко студио в центъра.",
        "services": ["Подстригване: дамско и мъжко", "Боядисване: модерни техники", "Прически за повод"],
        "phone": "+359 88 444 5566", "address": "ул. Гладстон 5", "lat": 42.1, "lon": 24.7,
    }, lambda s: True)
    assert not is_error and "Netlify Drop" in out
    page = settings.vault.parent / "Jarvis Sites" / "studio-mariya-plovdiv" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert opened == [page.as_uri()]
    assert "Студио &lt;Мария&gt;" in html and "<Мария>" not in html
    assert 'href="tel:+359884445566"' in html and "ул. Гладстон 5, Пловдив" in html
    assert html.count('class="card"') == 3 and "<h3>Прически за повод</h3>" in html
    assert "marker=42.1,24.7" in html and "Демо версия" in html
    assert "--a: #d6457a" in html  # the beauty look
