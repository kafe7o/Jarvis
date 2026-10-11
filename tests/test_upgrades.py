"""Jarvis changing his own code (upgrades.py, plugins/upgrade.py): backup, check, undo, after an update."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from conftest import Approver
from test_router import steps

from jarvis import upgrades
from jarvis.plugins import upgrade as upgrade_plugin

REPO = Path(__file__).resolve().parent.parent


def ok(_root):
    return None


@pytest.fixture
def root(tmp_path):
    """A small stand-in for the program folder."""
    base = tmp_path / "program"
    (base / "jarvis" / "web").mkdir(parents=True)
    (base / "jarvis" / "brain.py").write_text('GREETING = "Добър ден"\nLEVEL = 1\n', encoding="utf-8")
    (base / "jarvis" / "web" / "index.html").write_text("<html><script>go()</script></html>", encoding="utf-8")
    return base


def text(root, rel):
    return (root / rel).read_text(encoding="utf-8")


def test_an_upgrade_keeps_a_copy_and_can_be_undone(tmp_path, root):
    home = tmp_path / "home"
    record = upgrades.apply(home, "Поздравява с „Здравей“", [
        {"path": "jarvis/brain.py", "find": '"Добър ден"', "replace": '"Здравей"'},
        {"path": "plugins/hello.py", "content": "def register(registry, ctx):\n    pass\n"},
    ], root=root, checker=ok)
    assert record["state"] == "on" and record["id"] == 1
    assert set(record["files"]) == {"jarvis/brain.py", "jarvis/plugins/hello.py"}
    assert 'GREETING = "Здравей"' in text(root, "jarvis/brain.py") and (root / "jarvis/plugins/hello.py").exists()
    assert text(home, "upgrades/1/before/jarvis/brain.py").startswith('GREETING = "Добър ден"')

    second = upgrades.apply(home, "Ниво 2", [{"path": "jarvis/brain.py", "find": "LEVEL = 1", "replace": "LEVEL = 2"}],
                            root=root, checker=ok)
    with pytest.raises(ValueError, match="върни първо нея"):
        upgrades.undo(home, 1, root=root, checker=ok)  # the later one changed the same file
    assert upgrades.undo(home, root=root, checker=ok)["id"] == second["id"]
    assert upgrades.undo(home, root=root, checker=ok)["id"] == 1
    assert text(root, "jarvis/brain.py") == 'GREETING = "Добър ден"\nLEVEL = 1\n'
    assert not (root / "jarvis/plugins/hello.py").exists()
    assert [r["state"] for r in upgrades.records(home)] == ["off", "off"]
    with pytest.raises(ValueError, match="Няма надстройки"):
        upgrades.undo(home, root=root, checker=ok)
    assert "върната" in upgrades.describe(upgrades.records(home)[0])


def test_a_change_that_fails_the_check_or_does_not_fit_changes_nothing(tmp_path, root):
    home = tmp_path / "home"
    before = text(root, "jarvis/brain.py")
    record = upgrades.apply(home, "Счупено", [{"path": "jarvis/brain.py", "find": "LEVEL = 1", "replace": "LEVEL ="}],
                            root=root, checker=lambda _root: "SyntaxError: invalid syntax")
    assert record["state"] == "failed" and "SyntaxError" in record["error"]
    assert text(root, "jarvis/brain.py") == before

    bad = [
        ([{"path": "jarvis/brain.py", "find": "няма го", "replace": "x"}], "0 пъти"),
        ([{"path": "jarvis/brain.py", "find": "L", "replace": "x"}], "2 пъти"),
        ([{"path": "jarvis/upgrades.py", "content": ""}], "пази връщането"),
        ([{"path": "../.env", "content": "KEY=1"}], "Не може"),
        ([{"path": "jarvis/run.exe", "content": ""}], "само код"),
        ([{"path": "jarvis/brain.py"}], "content"),
        ([], "Няма промени"),
    ]
    for changes, why in bad:
        with pytest.raises(ValueError, match=why):
            upgrades.apply(home, "x", changes, root=root, checker=ok)
    assert text(root, "jarvis/brain.py") == before and not (tmp_path / ".env").exists()


def test_the_real_check_runs_the_changed_program(tmp_path):
    root = tmp_path / "program"
    shutil.copytree(REPO / "jarvis", root / "jarvis", ignore=shutil.ignore_patterns("__pycache__"))
    home = tmp_path / "home"
    broken = upgrades.apply(home, "Счупен инструмент", [{
        "path": "jarvis/plugins/leads.py", "find": "def register(registry: ToolRegistry, ctx) -> None:\n",
        "replace": "def register(registry: ToolRegistry, ctx) -> None:\n    raise RuntimeError('счупено')\n"}], root=root)
    assert broken["state"] == "failed" and "счупено" in broken["error"]
    assert "raise RuntimeError" not in text(root, "jarvis/plugins/leads.py")
    good = upgrades.apply(home, "Нов поздрав", [{
        "path": "jarvis/plugins/hello.py",
        "content": "from jarvis.tools import obj\n\ndef register(registry, ctx):\n"
                   "    @registry.tool('Say hello.', obj({}))\n    def say_hello():\n        return 'Здравей'\n"}], root=root)
    assert good["state"] == "on"


def test_after_an_update_the_upgrades_are_applied_again(tmp_path, root):
    home = tmp_path / "home"
    upgrades.apply(home, "Поздрав", [{"path": "jarvis/brain.py", "find": '"Добър ден"', "replace": '"Здравей"'},
                                     {"path": "jarvis/plugins/hello.py", "content": "X = 1\n"}], root=root, checker=ok)
    upgrades.apply(home, "Ниво", [{"path": "jarvis/brain.py", "find": "LEVEL = 1", "replace": "LEVEL = 5"}],
                   root=root, checker=ok)
    upgrades.started(home)
    # the new version from GitHub: brain.py changed elsewhere and no longer has LEVEL = 1; hello.py is gone
    (root / "jarvis/brain.py").write_text('import os\nGREETING = "Добър ден"\nLEVEL = 3\n', encoding="utf-8")
    (root / "jarvis/plugins/hello.py").unlink()

    said = upgrades.reapply(home, root, checker=ok)
    assert text(root, "jarvis/brain.py") == 'import os\nGREETING = "Здравей"\nLEVEL = 3\n'
    assert text(root, "jarvis/plugins/hello.py") == "X = 1\n"
    assert len(said) == 1 and "Надстройка 2" in said[0]
    assert [r["state"] for r in upgrades.records(home)] == ["on", "redo"]
    assert upgrades.started(home) == ["Надстройка 2 („Ниво“): не пасва на новата версия; кажи ми да я направя наново."]
    assert upgrades.started(home) == []  # told once

    upgrades.undo(home, 2, root=root, checker=ok)  # one that no longer fits is just switched off
    upgrades.undo(home, root=root, checker=ok)
    assert text(root, "jarvis/brain.py") == 'import os\nGREETING = "Добър ден"\nLEVEL = 3\n'

    upgrades.apply(home, "Поздрав 2", [{"path": "jarvis/brain.py", "find": '"Добър ден"', "replace": '"Ехо"'}],
                   root=root, checker=ok)
    (root / "jarvis/brain.py").write_text('GREETING = "Добър ден"\n', encoding="utf-8")
    assert "махнах ги" in upgrades.reapply(home, root, checker=lambda _root: "ImportError")[0]
    assert text(root, "jarvis/brain.py") == 'GREETING = "Добър ден"\n'  # the new version as it came
    assert upgrades.records(home)[-1]["state"] == "redo"


def test_jarvis_undoes_an_upgrade_he_cannot_start_with(tmp_path, root):
    home = tmp_path / "home"
    assert upgrades.recover(home, root) is None
    upgrades.apply(home, "Поздрав", [{"path": "jarvis/brain.py", "find": '"Добър ден"', "replace": '"Здравей"'}],
                   root=root, checker=ok)
    assert upgrades.recover(home, root)["state"] == "off"
    assert '"Добър ден"' in text(root, "jarvis/brain.py")
    assert "не тръгна" in upgrades.started(home)[0]

    upgrades.apply(home, "Поздрав", [{"path": "jarvis/brain.py", "find": '"Добър ден"', "replace": '"Здравей"'}],
                   root=root, checker=ok)
    upgrades.started(home)  # it started fine: kept from now on
    assert upgrades.recover(home, root) is None and '"Здравей"' in text(root, "jarvis/brain.py")


def test_the_tools_always_ask_the_owner_before_jarvis_changes_himself(registry, ctx, root, monkeypatch):
    monkeypatch.setattr(upgrades, "source_root", lambda: root)
    monkeypatch.setattr(upgrades, "check", ok)
    tools = ["read_own_code", "upgrade_self", "undo_upgrade", "list_upgrades", "update_jarvis"]
    assert {registry.tools[name].group for name in tools} == {"agent"}

    listing, _ = registry.run("read_own_code", {}, Approver())
    assert "jarvis/brain.py" in listing and "jarvis/web/index.html" in listing
    assert registry.run("read_own_code", {"path": "brain.py", "find": "level"}, Approver())[0].endswith("LEVEL = 1")
    assert registry.run("read_own_code", {"path": "../.env"}, Approver())[1]

    change = {"what": "Поздравява с „Здравей“", "changes": [{"path": "jarvis/brain.py", "find": '"Добър ден"', "replace": '"Здравей"'}]}
    no = Approver(False)
    out, is_error = registry.run("upgrade_self", change, no, trust_local=True)  # even when actions are always allowed
    assert is_error and len(no.asked) == 1 and "Надстройка на Jarvis: Поздравява" in no.asked[0] and '"Здравей"' in no.asked[0]
    assert '"Добър ден"' in text(root, "jarvis/brain.py")

    out, is_error = registry.run("upgrade_self", change, Approver())
    assert not is_error and "Upgrade 1 is in" in out and "restart Jarvis" in out
    assert '"Здравей"' in text(root, "jarvis/brain.py")
    assert "1. Поздравява с „Здравей“" in registry.run("list_upgrades", {}, Approver())[0]
    out, _ = registry.run("undo_upgrade", {}, Approver())
    assert out.startswith("Върнах надстройка 1") and '"Добър ден"' in text(root, "jarvis/brain.py")


def test_undo_and_update_by_voice_without_ai():
    assert steps("върни последната надстройка") == [("undo_upgrade", {})]
    assert steps("vurni nadstroikata") == [("undo_upgrade", {})]
    assert steps("обнови се") == [("update_jarvis", {})]
    assert steps("провери за нова версия") == [("update_jarvis", {})]
    assert steps("обнови се", owner=False) is None


def test_the_app_restarts_only_after_the_answer(settings, ctx, registry):
    from test_hub import make_hub

    hub, _port = make_hub(settings, ctx, registry, [])
    restarted = []
    hub.restart = lambda: restarted.append(time.time())
    hub.busy = 1
    hub.restart_when_idle(pause=0.01)
    time.sleep(0.2)
    assert restarted == []
    hub.busy = 0
    deadline = time.time() + 5
    while not restarted and time.time() < deadline:
        time.sleep(0.05)
    assert len(restarted) == 1


def test_the_plugin_map_names_real_files():
    for rel in upgrade_plugin.MAP:
        assert (REPO / rel).exists(), rel
