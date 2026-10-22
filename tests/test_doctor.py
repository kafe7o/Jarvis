"""The doctor: what works, what does not and how to fix it, in the owner's words."""

from __future__ import annotations

from conftest import Approver

from jarvis import doctor
from jarvis.brain import Jarvis


def test_the_report_puts_problems_first_and_ends_with_a_verdict():
    text = doctor.report([doctor.finding(doctor.OK, "Версия", "abc"), doctor.finding(doctor.BAD, "Мозък", "няма"),
                          doctor.finding(doctor.WARN, "Телефон", "не е свързан")])
    lines = text.splitlines()
    assert lines[0] == "1 неща не работят, 1 за оправяне."
    assert lines[1].startswith("❌ Мозък — няма") and lines[2].startswith("⚠️ Телефон") and lines[3].startswith("✅ Версия")
    assert doctor.report([doctor.finding(doctor.OK, "Всичко")]).startswith("Всичко важно работи.")


def test_the_doctor_says_what_the_laptop_brain_lacks(settings, ctx, monkeypatch):
    from jarvis import local, localmodels

    monkeypatch.setattr(localmodels, "version", lambda: "")
    monkeypatch.setattr(localmodels, "ollama_program", lambda: None)
    assert doctor.brains(ctx)[0]["state"] == doctor.BAD and "Инсталирай Ollama" in doctor.brains(ctx)[0]["hint"]
    monkeypatch.setattr(localmodels, "ollama_program", lambda: "C:/ollama.exe")
    assert "Пусни го" in doctor.brains(ctx)[0]["hint"]
    monkeypatch.setattr(localmodels, "version", lambda: "0.40.8")
    assert "няма модел" in doctor.brains(ctx)[0]["hint"]
    monkeypatch.setattr(local, "_seen", {"at": float("inf"), "models": ["huihui_ai/qwen3.5-abliterated:9b"]})
    found = {f["what"]: f for f in doctor.brains(ctx)}
    assert found["Мозък"]["state"] == doctor.OK and "qwen3.5-abliterated:9b" in found["Мозък"]["hint"]
    assert found["Бърз модел"]["state"] == found["Памет по смисъл"]["state"] == doctor.WARN
    monkeypatch.setattr(local, "_seen", {"at": float("inf"), "models": [
        "huihui_ai/qwen3.5-abliterated:9b", "huihui_ai/qwen3.5-abliterated:4B", "qwen3-embedding:0.6b"]})
    assert [f["state"] for f in doctor.brains(ctx)] == [doctor.OK]


def test_a_self_improvement_loop_jarvis_scheduled_is_flagged_as_waste(ctx, registry):
    registry.run("schedule_job", {"instruction": "Self-improvement итерация: прегледай се и се подобри",
                                  "at": "2026-01-01T13:30", "repeat": "hourly"}, Approver())
    registry.run("schedule_job", {"instruction": "Провери пощата", "at": "2026-01-01T08:00", "repeat": "daily"}, Approver())
    found = doctor.jobs(ctx.store)
    assert found[0]["hint"] == "2"
    flagged = [f for f in found if f["state"] == doctor.WARN]
    assert len(flagged) == 1 and "Self-improvement" in flagged[0]["what"] and "изтрий я" in flagged[0]["hint"]


def test_failed_steps_of_the_last_day_are_counted_by_tool(ctx):
    for ok in (1, 1, 0, 0, 1):
        ctx.store.insert("activity", agent="Jarvis", tool="read_window" if not ok else "web_browser", summary="x", ok=ok)
    found = doctor.failures(ctx.store)[0]
    assert found["state"] == doctor.BAD and "read_window ×2" in found["hint"] and "review_myself" in found["hint"]


def test_the_tool_is_the_owners_and_one_broken_check_does_not_hide_the_rest(settings, ctx, registry, monkeypatch):
    from jarvis.brain import TURN

    TURN.set(None)  # the owner's own request, whatever an earlier test left in this thread
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True))
    monkeypatch.setattr(doctor, "phone", lambda: 1 / 0)
    monkeypatch.setattr(doctor, "brains", lambda ctx: [doctor.finding(doctor.OK, "Мозък", "мисли с q")])
    out, err = registry.run("run_doctor", {}, Approver())
    assert not err, out
    assert "Една проверка не мина — ZeroDivisionError" in out and "Версия" in out and "Папка с данни" in out
    assert "Мозък — мисли с q" in out and "Задачи по график" in out
    assert "k" * 10 not in out  # no key is ever printed


def test_a_model_fixed_on_the_slider_has_no_quick_partner(ctx, monkeypatch):
    from jarvis import local, localmodels

    monkeypatch.setattr(localmodels, "version", lambda: "0.12")
    monkeypatch.setattr(local, "pick", lambda wanted=None: wanted or "qwen3.5:9b")
    monkeypatch.setattr(local, "quick_partner", lambda name: "qwen3.5:4b")
    monkeypatch.setattr(local, "embedder", lambda: "qwen3-embedding:0.6b")
    ctx.settings.local_model = "qwen3.5:9b"
    assert "кратките" not in doctor.brains(ctx)[0]["hint"]
    ctx.settings.local_model = ""
    assert "кратките отговори на qwen3.5:4b" in doctor.brains(ctx)[0]["hint"]
