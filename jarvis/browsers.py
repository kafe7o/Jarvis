"""The owner's own browser: which one it is, and how a page is opened in it as a new tab.

Jarvis has his own Playwright browser (plugins/browser.py), but the owner wants pages in *their* browser, with
their logins, and the laptop's log showed the brain hunting for AvastBrowser.exe over the whole disk, starting it
with subprocess or ``start``, or opening the default one by mistake. One tool path (open_target) opens any address
in the browser chosen here, so no step ever has to look for the program.

Which browser: the JARVIS_BROWSER setting (a name like "avast" or a full path), else the browser the owner's
remembered facts name most ("ползвай само Avast"), else the system default, else the first known one installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import webbrowser

# name, the words that mean it, Windows paths, macOS paths, Linux commands
KNOWN = [
    ("Avast Secure Browser", r"avast", [
        r"%ProgramFiles%\Avast Software\Browser\Application\AvastBrowser.exe",
        r"%ProgramFiles(x86)%\Avast Software\Browser\Application\AvastBrowser.exe",
        r"%LocalAppData%\AVAST Software\Browser\Application\AvastBrowser.exe",
    ], [], []),
    ("Google Chrome", r"chrome|хром", [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ], ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"], ["google-chrome", "google-chrome-stable"]),
    ("Microsoft Edge", r"edge|едж", [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ], ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"], ["microsoft-edge"]),
    ("Brave", r"brave|брейв", [
        r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ], ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"], ["brave-browser"]),
    ("Firefox", r"firefox|файърфокс|фаерфокс", [
        r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
    ], ["/Applications/Firefox.app/Contents/MacOS/firefox"], ["firefox"]),
    ("Opera", r"opera|опера", [
        r"%LocalAppData%\Programs\Opera\opera.exe", r"%LocalAppData%\Programs\Opera\launcher.exe",
    ], ["/Applications/Opera.app/Contents/MacOS/Opera"], ["opera"]),
    ("Vivaldi", r"vivaldi|вивалди", [r"%LocalAppData%\Vivaldi\Application\vivaldi.exe"],
     ["/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"], ["vivaldi"]),
    ("Chromium", r"chromium", [], [], ["chromium", "chromium-browser"]),
]
NAMES = re.compile("|".join(f"(?P<b{n}>{words})" for n, (_name, words, *_rest) in enumerate(KNOWN)), re.I)

# Things a brain writes when it tries to start or find a browser itself instead of calling open_target
BY_HAND = re.compile(
    r"(?i)AvastBrowser|Avast Software|\bavast\b|browser\.exe|chrome\.exe|msedge\.exe|firefox\.exe|brave\.exe|opera\.exe"
    r"|webbrowser\.open|\bstart\s+(\"\"\s*)?\"?https?://|os\.startfile\(\s*[\"']https?://"
    r"|Shell\\?Associations|UrlAssociations")
AUTOMATION = re.compile(r"(?i)\bpyautogui\b|\bpynput\b|\bpydirectinput\b|SendKeys|mouse_event\(|SetCursorPos")


def _paths(entry) -> list[str]:
    _name, _words, win, mac, linux = entry
    if sys.platform.startswith("win"):
        return [os.path.expandvars(p) for p in win]
    if sys.platform == "darwin":
        return list(mac)
    return [shutil.which(cmd) or "" for cmd in linux]


def installed(entry) -> str | None:
    """The program of this known browser, if it is installed here."""
    return next((p for p in _paths(entry) if p and os.path.exists(p)), None)


def named(text: str) -> str | None:
    """The known browser ``text`` names ("avast", "ползвай Chrome"), by name."""
    found = NAMES.search(text or "")
    if not found:
        return None
    return next(KNOWN[int(k[1:])][0] for k, v in found.groupdict().items() if v)


def wanted_by_facts(facts) -> str | None:
    """The browser the owner's remembered facts name most often, if one is named more than any other
    ("забранява Chrome; предпочита Avast" names Avast once, Chrome once; "само Avast" tips it)."""
    counts: dict[str, int] = {}
    for fact in facts or []:
        for found in NAMES.finditer(str(fact)):
            name = next(KNOWN[int(k[1:])][0] for k, v in found.groupdict().items() if v)
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def system_default() -> str | None:
    """The program the system opens https links with (Windows: the registry), when it can be read."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id + r"\shell\open\command") as key:
            command = winreg.QueryValueEx(key, None)[0]
    except Exception:
        return None
    found = re.match(r'\s*"([^"]+\.exe)"|\s*(\S+\.exe)', command, re.I)
    exe = found and (found.group(1) or found.group(2))
    return os.path.expandvars(exe) if exe and os.path.exists(os.path.expandvars(exe)) else None


def label(exe: str) -> str:
    """A browser's name from its program path, for the owner ("Avast Secure Browser"), else the file's name."""
    for name, words, *_rest in KNOWN:
        if re.search(words, exe, re.I):
            return name
    return os.path.splitext(re.split(r"[\\/]", exe)[-1])[0]


def find(facts=None) -> tuple[str, str] | None:
    """(name, program) of the owner's browser, or None when only the system's default opener is left."""
    chosen = os.environ.get("JARVIS_BROWSER", "").strip()
    if chosen and os.path.exists(os.path.expandvars(os.path.expanduser(chosen))):
        exe = os.path.expandvars(os.path.expanduser(chosen))
        return label(exe), exe
    want = named(chosen) if chosen else None
    if want is None and not chosen:
        want = wanted_by_facts(facts)
    if want:
        entry = next(e for e in KNOWN if e[0] == want)
        exe = installed(entry)
        if exe:
            return want, exe
    exe = system_default()
    if exe:
        return label(exe), exe
    for entry in KNOWN:
        exe = installed(entry)
        if exe:
            return entry[0], exe
    return None


def open_url(url: str, facts=None) -> str:
    """Open ``url`` in the owner's browser (a new tab of the running one) and say which browser that was."""
    url = url if "://" in url else "https://" + url
    found = find(facts)
    if found is None:
        webbrowser.open(url)
        return "the default browser"
    name, exe = found
    subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **({"start_new_session": True} if not sys.platform.startswith("win") else {}))
    return name


def by_hand(code: str) -> str | None:
    """Why a piece of code or a shell command must not run: it starts or hunts for a browser, or drives the mouse
    and keyboard itself. The tools that do it properly are named instead."""
    if BY_HAND.search(code or ""):
        return ("Not run: pages open with open_target, which uses the owner's browser and tells you its window; "
                "never start a browser by hand, search the disk for it or read the registry. Then work in it with "
                "read_window and click_text.")
    if AUTOMATION.search(code or ""):
        return ("Not run: the mouse and keyboard are click_text and control_input, the screen is read_window and "
                "look_at_screen; never drive them with pyautogui or similar code.")
    return None
