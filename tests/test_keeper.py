from __future__ import annotations

from types import SimpleNamespace

from jarvis import keeper


def test_the_keeper_starts_jarvis_again_until_he_is_stopped(tmp_path):
    codes, started, waits = [1, keeper.RESTART, keeper.STOP], [], []

    def popen(args, env, **kw):
        started.append((args, env.get(keeper.KEPT)))
        return SimpleNamespace(wait=lambda: codes.pop(0))

    keeper.run(tmp_path, popen=popen, sleep=waits.append)
    assert len(started) == 3 and all(a[-2:] == ["app", "--no-window"] and kept == "1" for a, kept in started)
    assert waits == [3]  # a crash: a short pause; a restart: at once; a stop: no more
    log = (tmp_path / "jarvis.log").read_text(encoding="utf-8")
    assert "exit code 1" in log and "jarvis stop" in log


def test_only_one_keeper_runs(tmp_path):
    held = keeper.lock(tmp_path / "keeper.lock")
    started = []
    keeper.run(tmp_path, popen=lambda *a, **kw: started.append(a), rounds=1)
    assert held is not None and not started  # the second keeper ends at once: never two Jarvis on one port
    held.close()
    assert keeper.lock(tmp_path / "keeper.lock") is not None


def test_a_jarvis_that_keeps_failing_is_started_more_slowly(tmp_path):
    waits = []
    keeper.run(tmp_path, popen=lambda *a, **kw: SimpleNamespace(wait=lambda: 1), sleep=waits.append, rounds=6)
    assert waits == [3, 3, 3, 3, 60, 60]
