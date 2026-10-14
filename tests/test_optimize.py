"""The optimizer (nightly audit), the work report and the disk map (from the owner's videos, 2026-10-11)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from jarvis import core_files, diskmap, optimize
from jarvis.routines import ROUTINES

from conftest import Approver


def test_the_audit_removes_duplicates_and_lists_contradictions(ctx, registry):
    store, home = ctx.store, ctx.settings.home
    store.insert("facts", topic="кафе", fact="без захар")
    store.insert("facts", topic="Кафе", fact="Без захар")  # the same, twice
    store.insert("facts", topic="кафе", fact="с една лъжичка захар")  # contradicts the first
    store.insert("facts", topic="кола", fact="синя")
    core_files.write(home, "LESSONS.md", "- Когато звъня, питам първо.\n- Когато звъня, питам първо.\n- Никога не затварям прозореца.")
    old = (datetime.now() - timedelta(days=30)).isoformat()
    store.execute("INSERT INTO plans (goal, steps, status, created) VALUES (?, ?, 'active', ?)",
                  ("Стар план", json.dumps([{"id": "1", "title": "x", "status": "todo", "note": ""}]), old))
    store.insert("contacts", name="Мама", phone="+359888111222")
    store.insert("contacts", name="Mom", phone="+359 888 111 222")
    (home / "skills").mkdir(parents=True, exist_ok=True)
    (home / "skills" / "bad.py").write_text("def register(registry, ctx):\n  return (\n", encoding="utf-8")
    store.insert("activity", agent="Jarvis", tool="read_file", summary="x", ok=0)

    found = optimize.audit(store, home, days=7)
    assert len(store.query("SELECT * FROM facts")) == 3 and found["facts"] == 3
    assert any("duplicate fact removed" in x for x in found["fixed"]) and any("duplicate lesson removed" in x for x in found["fixed"])
    assert core_files.read(home, "LESSONS.md").count("питам първо") == 1
    assert any("'кафе' may contradict" in x and "лъжичка" in x for x in found["conflicts"])
    assert any("Мама, Mom share the number 359888111222" in x for x in found["conflicts"])
    assert found["stale"] and "Стар план" in found["stale"][0]
    assert found["broken"] and "skill bad" in found["broken"][0]
    assert "1 failed" in found["failures"]
    text = optimize.report(found)
    assert text.startswith("# Одит на Jarvis") and "Противоречия" in text and "лъжичка" in text

    out, error = registry.run("audit_myself", {}, Approver())
    assert not error and "Одит на Jarvis" in out and "Saved as" in out
    assert any(p.name.startswith("audit-") for p in (ctx.settings.vault / "output").iterdir())
    assert ROUTINES["optimize"]["time"] == "04:00" and "audit_myself" in ROUTINES["optimize"]["instruction"]
    assert "work_report" in ROUTINES["morning"]["instruction"]


def test_the_work_report_counts_what_was_done(ctx, registry):
    store = ctx.store
    store.add_message("main", "user", "направи го")
    store.insert("activity", agent="Jarvis", tool="web_browser", summary="x", ok=1)
    store.insert("activity", agent="Проучвател", tool="google_search", summary="y", ok=0)
    store.insert("tasks", title="t", done=1)
    out, error = registry.run("work_report", {"days": 1}, Approver())
    assert not error
    assert "1 requests, 2 steps (1 failed), 1 tasks done" in out and "web_browser ×1" in out and "Проучвател" in out


def test_the_disk_map_names_the_biggest_folders_and_files(tmp_path, registry, ctx, monkeypatch):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "c").mkdir()
    (tmp_path / "a" / "b" / "big.bin").write_bytes(b"x" * 250_000_000)
    (tmp_path / "c" / "small.txt").write_text("hi" * 300_000)
    found = diskmap.scan(tmp_path)
    names = [p.rsplit("/", 1)[-1] for _s, p in found["folders"]]
    assert names[0] == "b" and "a" not in names and "c" in names  # a holds only b, so b is shown instead of a
    assert found["files"][0][1].endswith("big.bin") and not found["partial"]
    text = diskmap.summary(found)
    assert "Biggest folders:" in text and "250 MB" in text and "big.bin" in text
    page = diskmap.html(found)
    assert page.startswith("<!doctype html>") and 'class="box"' in page and "big.bin" in page

    opened = []
    monkeypatch.setattr("jarvis.plugins.system.webbrowser.open", opened.append)
    out, error = registry.run("disk_space", {"folder": str(tmp_path)}, Approver())
    assert not error and "The map is open on the screen" in out and opened and opened[0].startswith("file://")
    assert (ctx.settings.home / "disk-map.html").exists()


def test_connections_tool_shows_what_is_missing_and_save_setting_stores_a_key(settings, ctx, registry, tmp_path, monkeypatch):
    from jarvis import setup_wizard
    from jarvis.plugins.connections import page_of

    env = tmp_path / ".env"
    env.write_text("NVIDIA_API_KEY=nvapi-x\n")
    monkeypatch.setattr(setup_wizard, "env_path", lambda: env)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    report = registry.tools["connections"].func()
    assert "❌ Telegram" in report and "MISSING: TELEGRAM_BOT_TOKEN" in report
    assert "set: NVIDIA_API_KEY" in report and "https://build.nvidia.com" in report
    assert page_of("console.groq.com/keys > Create API Key (без карта)") == "https://console.groq.com/keys"
    assert page_of("@BotFather (токен) и @userinfobot (твоя id)") == ""
    said = registry.tools["save_setting"].func("telegram_bot_token", "123:abc")
    assert said.startswith("Saved TELEGRAM_BOT_TOKEN") and "restart" in said
    assert setup_wizard.read_env(env) == {"NVIDIA_API_KEY": "nvapi-x", "TELEGRAM_BOT_TOKEN": "123:abc"}
    assert "✅ Telegram" not in registry.tools["connections"].func("Telegram")  # the owner id is still missing
    assert registry.tools["save_setting"].func("NOT_A_KEY", "x").startswith("No setting is called NOT_A_KEY")
    said = registry.tools["save_setting"].func("JARVIS_TTS_VOICE", "Калина")  # a name, not a voice: nothing saved
    assert said.startswith("'Калина' is not a voice name") and "bg-BG-KalinaNeural" in said
    said = registry.tools["save_setting"].func("JARVIS_TTS_VOICE", "bg-BG-KalinaNeural")
    assert "now speaks with bg-BG-KalinaNeural" in said and setup_wizard.read_env(env)["JARVIS_TTS_VOICE"] == "bg-BG-KalinaNeural"
    tool = registry.tools["save_setting"]
    assert tool.confirm and tool.local and tool.group == "agent" and registry.tools["connections"].group == "core"
    assert "123:abc" not in tool.describe_call({"key": "TELEGRAM_BOT_TOKEN", "value": "123:abc"})  # the yes/no card hides it
