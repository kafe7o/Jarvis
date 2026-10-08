from __future__ import annotations

import json
from datetime import datetime, timedelta

from conftest import response, text_block, tool_block
from test_accounts import Browser
from test_hub import call, make_hub

from jarvis import memory_map


def fill(store, vault):
    store.insert("facts", topic="семейство", fact="Мария е сестра ми")
    store.insert("facts", topic="Семейство", fact="Иван е баща ми")
    store.insert("facts", topic="храна", fact="Обичам баница")
    store.insert("contacts", name="Мария", phone="+359888111222")
    store.insert("contacts", name="Ив", notes="колега")  # too short a name to look for
    store.insert("tasks", title="Купи цветя за Мария", due="2026-10-10")
    store.insert("tasks", title="Стара задача", done=1)
    store.insert("events", title="Рожден ден на Мария", start=(datetime.now() + timedelta(days=3)).isoformat())
    store.insert("events", title="Минало събитие", start="2020-01-01T10:00")
    store.insert("plans", goal="Ремонт", steps=json.dumps([{"id": "1", "status": "done"}, {"id": "2", "status": "todo"}]))
    (vault / "wiki").mkdir(parents=True)
    (vault / "raw" / "_done").mkdir(parents=True)
    (vault / "wiki" / "Шипка.md").write_text("# Шипка\nХижата, където ходихме с Мария. Виж [[Почивки]].", encoding="utf-8")
    (vault / "wiki" / "Почивки.md").write_text("# Почивки\nМоре и планина.", encoding="utf-8")
    (vault / "raw" / "_done" / "стара.md").write_text("обработена", encoding="utf-8")
    (vault / "README.md").write_text("как работи хранилището", encoding="utf-8")


def test_the_map_joins_what_belongs_together(ctx, settings):
    fill(ctx.store, settings.vault)
    graph = memory_map.build(ctx.store, settings.vault)
    nodes = {n["id"]: n for n in graph["nodes"]}
    links = {tuple(link) for link in graph["links"]}

    assert {"me", "facts", "people", "notes", "tasks", "calendar", "plans", "history"} <= set(nodes)
    assert ("facts", "topic:семейство") in links and ("topic:семейство", "fact:1") in links
    assert ("topic:семейство", "fact:2") in links  # one topic, whatever the capital letters
    assert graph["counts"]["topic"] == 2 and graph["counts"]["fact"] == 3
    assert [n["label"] for n in graph["nodes"] if n["kind"] == "task"] == ["Купи цветя за Мария"]
    assert [n["label"] for n in graph["nodes"] if n["kind"] == "event"] == ["Рожден ден на Мария"]
    assert nodes["plan:1"]["detail"] == "1 от 2 стъпки"
    assert sorted(n["label"] for n in graph["nodes"] if n["kind"] == "note") == ["Почивки", "Шипка"]
    assert nodes["folder:wiki"]["label"] == "Знание" and ("notes", "folder:wiki") in links
    assert ("note:wiki/Почивки.md", "note:wiki/Шипка.md") in links or ("note:wiki/Шипка.md", "note:wiki/Почивки.md") in links

    maria = {b for a, b in links if a == "person:1"}
    assert maria == {"fact:1", "task:1", "event:1", "note:wiki/Шипка.md"}
    assert not any(a == "person:2" for a, b in links)
    assert all(a in nodes and b in nodes for a, b in links)


def test_a_finished_step_says_which_points_it_read_or_wrote(ctx, settings):
    fill(ctx.store, settings.vault)
    graph = memory_map.build(ctx.store, settings.vault)

    result = json.dumps([{"id": 1, "topic": "семейство", "fact": "Мария е сестра ми"}], ensure_ascii=False)
    hub, verb, ids = memory_map.touched(graph, "recall", {"query": "Мария"}, result)
    assert (hub, verb) == ("facts", "read") and ids == ["topic:семейство", "fact:1"]

    assert memory_map.touched(graph, "remember", {"topic": "храна", "fact": "x"}, "Remembered (id 3).")[2][0] == "fact:3"
    assert memory_map.touched(graph, "vault_read", {"path": "wiki/Шипка.md"}, "...")[:2] == ("notes", "read")
    assert memory_map.touched(graph, "vault_read", {"path": "wiki/Шипка.md"}, "...")[2] == ["note:wiki/Шипка.md"]
    assert memory_map.touched(graph, "web_search", {"query": "Мария"}, "Мария") == (None, "", [])


def test_the_app_lights_the_memory_jarvis_uses_and_only_the_owner_sees_it(settings, ctx, registry):
    ctx.store.insert("facts", topic="семейство", fact="Мария е сестра ми")
    hub, port = make_hub(settings, ctx, registry, [
        response(tool_block("recall", {"query": "Мария"}), stop="tool_use"),
        response(tool_block("remember", {"topic": "семейство", "fact": "Мария обича лалета"}, id="tu_2"), stop="tool_use"),
        response(text_block("Мария е сестра ти и обича лалета.")),
    ])
    owner = hub.accounts.create("owner@example.com", "Анастас", "123456", role="owner")
    member = hub.accounts.create("m@example.com", "Мария", "123456")
    assert len(hub.memory_graph()["nodes"]) == 1 + len(memory_map.HUBS) + 2

    hub.ask("Какво знаеш за Мария?", user=owner)
    lit = [e for e in hub.events.events if e["kind"] == "memory"]
    assert [(e["verb"], e["hub"]) for e in lit] == [("think", "facts"), ("read", "facts"), ("write", "facts")]
    assert all(e["to"] is None for e in lit)
    assert lit[1]["nodes"] == ["topic:семейство", "fact:1"] and lit[1]["label"] == "Спомня си"
    assert lit[2]["nodes"][0] == "fact:2" and lit[2]["changed"]
    assert "fact:2" in {n["id"] for n in hub.memory_graph()["nodes"]}  # a write shows up at once

    _, body = call(port, "/api/memory?fresh=1")
    assert json.loads(body)["counts"]["fact"] == 2

    hub.track(member.name, "моята задача", to=member.id)  # a member's work lights nothing
    hub.board.reporter(hub.board.snapshot()["running"][0]["id"])({"type": "level", "level": 3})
    assert len([e for e in hub.events.events if e["kind"] == "memory"]) == 3

    maria = Browser(port)
    assert maria("/api/login", {"email": "m@example.com", "password": "123456"})[0] == 200
    assert maria("/api/memory")[0] == 403
