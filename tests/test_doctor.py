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


def test_without_keys_the_doctor_says_where_each_free_brain_comes_from(settings, ctx, registry):
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True))
    found = {f["what"]: f for f in doctor.brains(ctx, network=False)}
    assert found["Мозък Gemini"]["state"] == doctor.WARN and "aistudio" in found["Мозък Gemini"]["hint"]
    assert "build.nvidia.com" in found["Мозък NVIDIA"]["hint"] and "console.groq.com" in found["Мозък Groq"]["hint"]
    assert found["Мозък Claude"]["state"] == doctor.OK and "по твое желание" in found["Мозък Claude"]["hint"]
    assert found["Нито един безплатен мозък не може да отговори сега"]["state"] == doctor.BAD


def test_a_key_that_is_set_counts_even_when_the_brain_rests(settings, ctx, registry, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    ctx.jarvis = Jarvis(settings, ctx.store, registry, Approver(True))
    monkeypatch.setattr(ctx.jarvis.gemini, "models", lambda model, agent=False: [] if "flash" in model else ["x"])
    monkeypatch.setattr(ctx.jarvis.free_apis["nvidia"], "models", lambda model: [])
    found = {f["what"]: f for f in doctor.brains(ctx, network=True)}
    assert found["Мозък Gemini"]["state"] == doctor.WARN and "квота" in found["Мозък Gemini"]["hint"]
    assert found["Мозък NVIDIA"]["state"] == doctor.WARN and "почива" in found["Мозък NVIDIA"]["hint"]
    monkeypatch.setattr(ctx.jarvis.gemini, "models", lambda model, agent=False: [model])
    found = {f["what"]: f for f in doctor.brains(ctx, network=True)}
    assert found["Мозък Gemini"]["state"] == doctor.OK
    assert "Нито един безплатен мозък не може да отговори сега" not in found


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
    out, err = registry.run("run_doctor", {"network": False}, Approver())
    assert not err, out
    assert "Една проверка не мина — ZeroDivisionError" in out and "Версия" in out and "Папка с данни" in out
    assert "Мозък Gemini" in out and "Задачи по график" in out
    assert "k" * 10 not in out  # no key is ever printed
