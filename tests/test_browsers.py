"""Pages open in the owner's browser (Avast on the laptop) with one tool, never by hunting for the program."""

from __future__ import annotations

import sys

from conftest import Approver

from jarvis import browsers


def test_the_wanted_browser_comes_from_the_setting_then_the_facts_then_the_default(monkeypatch, tmp_path):
    exe = tmp_path / "AvastBrowser.exe"
    exe.write_text("")
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("")
    monkeypatch.setattr(browsers, "installed", lambda entry: {"Avast Secure Browser": str(exe), "Google Chrome": str(chrome)}.get(entry[0]))
    monkeypatch.setattr(browsers, "system_default", lambda: str(chrome))
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    assert browsers.find() == ("Google Chrome", str(chrome))  # nothing said: the system's default
    facts = ["Потребителят изрично забранява използването на Chrome; предпочита браузъра Avast",
             "Всяко отваряне на уебсайт или браузър трябва да става изключително през браузъра Avast"]
    assert browsers.find(facts) == ("Avast Secure Browser", str(exe))  # what he taught Jarvis counts
    assert browsers.wanted_by_facts(["Chrome е забранен", "Avast е добър"]) is None  # a tie decides nothing
    monkeypatch.setenv("JARVIS_BROWSER", "edge")  # the setting wins, and a browser that is not installed is skipped
    assert browsers.find(facts) == ("Google Chrome", str(chrome))
    monkeypatch.setenv("JARVIS_BROWSER", "avast")
    assert browsers.find() == ("Avast Secure Browser", str(exe))
    monkeypatch.setenv("JARVIS_BROWSER", str(chrome))  # a full path
    assert browsers.find(facts) == ("Google Chrome", str(chrome))
    monkeypatch.setattr(browsers, "system_default", lambda: None)
    monkeypatch.setenv("JARVIS_BROWSER", "")
    assert browsers.find() == ("Avast Secure Browser", str(exe))  # no default known: the first installed one
    monkeypatch.setattr(browsers, "installed", lambda entry: None)
    assert browsers.find() is None


def test_open_url_starts_a_tab_in_that_browser_and_says_which(monkeypatch, tmp_path):
    exe = tmp_path / "AvastBrowser.exe"
    exe.write_text("")
    started = []
    monkeypatch.setattr(browsers, "find", lambda facts=None: ("Avast Secure Browser", str(exe)))
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda args, **kw: started.append(args))
    assert browsers.open_url("github.com/trending") == "Avast Secure Browser"
    assert started == [[str(exe), "https://github.com/trending"]]
    opened = []
    monkeypatch.setattr(browsers, "find", lambda facts=None: None)
    monkeypatch.setattr(browsers.webbrowser, "open", opened.append)
    assert browsers.open_url("https://x.y") == "the default browser" and opened == ["https://x.y"]


def test_the_default_browser_is_read_from_the_registry_only_on_windows(monkeypatch):
    monkeypatch.setattr(browsers.sys, "platform", "linux")
    assert browsers.system_default() is None
    assert browsers.label(r"C:\Program Files\Avast Software\Browser\Application\AvastBrowser.exe") == "Avast Secure Browser"
    assert browsers.label(r"C:\Tools\mybrowser.exe") == "mybrowser"
    assert browsers.named("ползвай само Avast") == "Avast Secure Browser" and browsers.named("нищо") is None


def test_the_tool_opens_pages_in_the_owners_browser_and_remembers_what_he_was_taught(registry, ctx, monkeypatch):
    started = []
    monkeypatch.setattr(browsers, "find", lambda facts=None: ("Avast Secure Browser", "avast.exe") if any("Avast" in f for f in facts or []) else None)
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(browsers.webbrowser, "open", lambda url: started.append(["default", url]))
    out, err = registry.run("open_target", {"target": "https://www.google.com/recaptcha/api2/demo"}, Approver())
    assert not err and started[-1][0] == "default" and 'read_window("браузъра")' in out
    registry.run("remember", {"topic": "браузър", "fact": "Ползва само Avast"}, Approver())
    out, err = registry.run("open_target", {"target": "www.google.com/recaptcha/api2/demo"}, Approver())
    assert not err and started[-1] == ["avast.exe", "https://www.google.com/recaptcha/api2/demo"]
    assert "Opened www.google.com/recaptcha/api2/demo in Avast Secure Browser" in out and 'read_window("Avast")' in out
    out, err = registry.run("open_target", {"target": "github.com/trending"}, Approver())
    assert not err and started[-1] == ["avast.exe", "https://github.com/trending"]  # a bare domain is a page
    out, err = registry.run("open_target", {"target": "report.pdf"}, Approver())
    assert err or started[-1] != ["avast.exe", "https://report.pdf"]  # a file name is not a website


def test_code_that_starts_or_hunts_for_a_browser_or_drives_the_mouse_is_refused(registry):
    """The laptop's log: run_python with subprocess.Popen(AvastBrowser.exe), os.walk over Program Files looking for
    the browser, run_shell `where /r "C:\\Program Files" browser.exe` (150 seconds) and `start "" "https://..."`,
    pyautogui in run_python. Each costs a step or a minute and opens the wrong browser; the tool says what to use."""
    for code in ['import subprocess\nsubprocess.Popen([r"C:\\Program Files\\Avast Software\\Browser\\Application\\AvastBrowser.exe", "https://x"])',
                 'import os\n# Try to find Avast browser path\nfor root, dirs, files in os.walk(r"C:\\Program Files"): pass',
                 "import webbrowser; webbrowser.open('https://x')"]:
        out, err = registry.run("run_python", {"code": code}, Approver())
        assert err and "open_target" in out, code
    for command in ['where /r "C:\\Program Files" browser.exe', 'start "" "https://www.google.com/recaptcha/api2/demo"', "start https://x.y"]:
        out, err = registry.run("run_shell", {"command": command}, Approver())
        assert err and "open_target" in out, command
    out, err = registry.run("run_python", {"code": "import pyautogui\npyautogui.click(10, 10)"}, Approver())
    assert err and "click_text and control_input" in out
    out, err = registry.run("run_python", {"code": "print(2 + 2)"}, Approver())
    assert not err and out.strip().endswith("4")
    out, err = registry.run("run_shell", {"command": f"{sys.executable} -c \"print('hi')\""}, Approver())
    assert not err and "hi" in out


def test_open_page_opens_the_page_for_the_owner_and_brings_its_text_in_the_same_step(registry, monkeypatch):
    """The slow way was open, read the window, look again: one brain step each. open_page answers with the text."""
    from jarvis import winui
    from jarvis.plugins import daily

    started = []
    monkeypatch.setattr(browsers, "find", lambda facts=None: ("Avast Secure Browser", "avast.exe"))
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda args, **kw: started.append(args))
    rows = "".join(f"<tr><td>Валута {i}</td><td>CUR{i}</td><td>{i}.0000</td></tr>" for i in range(200))
    page = ("<html><head><title>БНБ - Курсове</title><script>var x = 'USD 9.99';</script></head><body><nav>Начало"
            f"</nav><table>{rows}<tr><td>Щатски долар</td><td>USD</td><td>1.6702</td></tr></table></body></html>")
    monkeypatch.setattr(daily, "fetch", lambda url, timeout=10, cookie=None: page)
    out, err = registry.run("open_page", {"url": "bnb.bg/kursove", "find": "USD"}, Approver())
    assert not err and started[-1] == ["avast.exe", "https://bnb.bg/kursove"]
    assert 'The page "БНБ - Курсове" says' in out and "Щатски долар USD 1.6702" in out and "9.99" not in out
    assert len(out) < 1500  # only the part around the word, not the whole page

    out, err = registry.run("open_page", {"url": "https://bnb.bg/kursove"}, Approver())
    assert not err and "Валута 0 CUR0" in out and len(out) < 4500  # no word asked: the start of the page

    # A page made only of scripts (or a sign-in) is read on the screen after the tab loads.
    monkeypatch.setattr(daily, "fetch", lambda url, timeout=10, cookie=None: "<html><body><div id=app></div></body></html>")
    monkeypatch.setattr(winui, "on_windows", lambda: True)
    monkeypatch.setattr(winui, "read", lambda title=None: f'Window: "{title}" link "Резултати"')
    monkeypatch.setattr("jarvis.plugins.system.time.sleep", lambda s: None)
    out, err = registry.run("open_page", {"url": "flashscore.bg"}, Approver())
    assert not err and 'The browser window shows now:\nWindow: "Avast" link "Резултати"' in out
