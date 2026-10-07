"""Android phones and Android/Google TVs over adb (USB or Wi-Fi).

Phones plugged in by USB, or paired once over Wi-Fi (android_wireless), are found by themselves; the
first is "phone". Devices can also be named in JARVIS_ADB_DEVICES="phone=192.168.1.20:5555,tv=192.168.1.30:5555"
(enable Developer options > USB / Wireless debugging on each device; first connection asks to allow this
computer). On the phone this gives Jarvis your own mobile number: it can call from it, answer and hang
up, send SMS, read the screen and tap like a person, type in Bulgarian, read messages, calls and
contacts, and move files between the phone and the computer.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from ..procs import run_capture
from ..tools import Image, ToolRegistry, obj
from . import NotConfigured, save_settings

KEYS = {
    "home": "KEYCODE_HOME", "back": "KEYCODE_BACK", "power": "KEYCODE_POWER", "wake": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP", "volume_up": "KEYCODE_VOLUME_UP", "volume_down": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE", "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE", "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS", "up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT", "right": "KEYCODE_DPAD_RIGHT", "ok": "KEYCODE_DPAD_CENTER",
    "enter": "KEYCODE_ENTER", "answer_call": "KEYCODE_CALL", "end_call": "KEYCODE_ENDCALL",
    "channel_up": "KEYCODE_CHANNEL_UP", "channel_down": "KEYCODE_CHANNEL_DOWN", "input": "KEYCODE_TV_INPUT",
    "recent_apps": "KEYCODE_APP_SWITCH", "delete": "KEYCODE_DEL", "camera": "KEYCODE_CAMERA",
}

APPS = {
    "youtube": "com.google.android.youtube", "youtube_tv": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja", "spotify": "com.spotify.music", "chrome": "com.android.chrome",
    "maps": "com.google.android.apps.maps", "whatsapp": "com.whatsapp", "viber": "com.viber.voip",
    "messenger": "com.facebook.orca", "camera": "android.media.action.STILL_IMAGE_CAMERA",
    "tiktok": "com.zhiliaoapp.musically", "instagram": "com.instagram.android", "facebook": "com.facebook.katana",
    "telegram": "org.telegram.messenger", "gmail": "com.google.android.gm", "photos": "com.google.android.apps.photos",
    "play_store": "com.android.vending", "settings": "android.settings.SETTINGS", "dialer": "android.intent.action.DIAL",
}

# Free, open-source keyboard that lets adb type any language (adb's own "input text" is ASCII only).
KEYBOARD = "com.android.adbkeyboard/.AdbIME"
KEYBOARD_APK = "https://github.com/senzhk/ADBKeyBoard/releases/download/v2.4-dev/keyboardservice-debug.apk"
WIRELESS = "_adb-tls-connect"  # how adb names phones it found on Wi-Fi after pairing
pause = time.sleep


def connected() -> list[str]:
    """Serials adb can talk to right now (USB, paired Wi-Fi and adb connect)."""
    if not shutil.which("adb"):
        return []
    code, out, _ = run_capture(["adb", "devices"], timeout=15)
    if code != 0:
        return []
    return [line.split()[0] for line in out.decode(errors="replace").splitlines()[1:] if line.strip().endswith("device")]


def configured_devices() -> dict[str, str]:
    """Devices named in JARVIS_ADB_DEVICES."""
    out = {}
    for item in filter(None, (p.strip() for p in os.environ.get("JARVIS_ADB_DEVICES", "").split(","))):
        name, _, serial = item.partition("=")
        out[name.strip()] = serial.strip()
    return out


def devices_from_env() -> dict[str, str]:
    """Devices named in JARVIS_ADB_DEVICES, plus any other phone adb sees (plugged in or paired over Wi-Fi):
    the first of those is "phone", the next "phone2" and so on."""
    out = configured_devices()
    n = 1
    for serial in connected():
        if serial in out.values():
            continue
        while (name := "phone" if n == 1 else f"phone{n}") in out:
            n += 1
        out[name] = serial
    return out


def adb(serial: str, *args: str, timeout: float = 30, binary: bool = False):
    if not shutil.which("adb"):
        raise NotConfigured("Android control", ["install Android platform-tools (adb)"])
    if ":" in serial and WIRELESS not in serial:
        run_capture(["adb", "connect", serial], timeout=15)
    code, out, err = run_capture(["adb", "-s", serial, *args], timeout=timeout, merge=False)
    if code is None:
        raise RuntimeError(f"adb did not answer in {timeout:g} seconds. Is the device on and allowed?")
    if code:
        raise RuntimeError(err.decode(errors="replace").strip() or out.decode(errors="replace").strip() or "adb failed")
    return out if binary else out.decode(errors="replace")


def screen_elements(xml: str) -> list[dict]:
    """What is on the phone's screen, from a uiautomator dump: every labelled or tappable element."""
    root = ElementTree.fromstring(xml[xml.find("<"):xml.rfind(">") + 1])
    out = []
    for node in root.iter("node"):
        label = (node.get("text") or "").strip() or (node.get("content-desc") or "").strip()
        clickable = node.get("clickable") == "true" or node.get("long-clickable") == "true"
        kind = (node.get("class") or "").rpartition(".")[2]
        if not label and not (clickable and node.get("resource-id")):
            continue
        box = [int(n) for n in re.findall(r"-?\d+", node.get("bounds") or "")]
        if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
            continue
        out.append({
            "label": label or node.get("resource-id", "").rpartition("/")[2].replace("_", " "),
            "x": (box[0] + box[2]) // 2, "y": (box[1] + box[3]) // 2, "kind": kind, "tap": clickable,
            "field": kind == "EditText", "checked": node.get("checked") == "true" if node.get("checkable") == "true" else None,
        })
    return out


def describe_elements(elements: list[dict]) -> str:
    lines = []
    for e in elements[:200]:
        extra = ", text field" if e["field"] else ""
        extra += "" if e["checked"] is None else (", on" if e["checked"] else ", off")
        extra += ", tappable" if e["tap"] else ""
        lines.append(f'- "{e["label"][:120]}" {e["kind"]} at {e["x"]},{e["y"]}{extra}')
    return "\n".join(lines) or "Nothing readable on the screen (maybe a game, video or the lock screen); use look."


def content_rows(output: str, keys: list[str]) -> list[dict]:
    """Rows printed by `content query` ("Row: 0 address=+359…, body=Здрасти, ще закъснея, date=…").
    Columns come in the projection's order, so each value runs up to the next column's name; that keeps
    commas and new lines inside a message intact."""
    rows = []
    for chunk in re.split(r"(?m)^Row: \d+ ", output)[1:]:
        row, start = {}, 0
        for i, key in enumerate(keys):
            begin = chunk.find(f"{key}=", start) if i == 0 else chunk.find(f", {key}=", start)
            if begin < 0:
                break
            begin += len(key) + (1 if i == 0 else 3)
            nxt = chunk.find(f", {keys[i + 1]}=", begin) if i + 1 < len(keys) else -1
            end = nxt if nxt >= 0 else len(chunk)
            value = chunk[begin:end].rstrip("\r\n")
            row[key] = None if value == "NULL" else value
            start = end
        rows.append(row)
    return rows


def when(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return "?"


def register(registry: ToolRegistry, ctx) -> None:
    def serial_of(device: str) -> str:
        devices = devices_from_env()
        if not devices:
            raise NotConfigured("Android phone/TV", [
                "plug the phone in by USB with USB debugging on, or pair it over Wi-Fi with android_wireless"])
        if device not in devices:
            raise ValueError(f"Unknown device '{device}'. Known: {', '.join(devices)}")
        return devices[device]

    def shell(device: str, command: str, timeout: float = 30) -> str:
        return adb(serial_of(device), "shell", command, timeout=timeout)

    def find_app(device: str, name: str) -> str:
        key = (name or "").strip().lower().replace(" ", "_")
        if key in APPS:
            return APPS[key]
        if "." in name and " " not in name.strip():
            return name.strip()  # already a package or an intent action
        wanted = re.sub(r"[^a-z0-9]", "", key)
        packages = [line.partition(":")[2].strip() for line in shell(device, "pm list packages").splitlines()
                    if line.startswith("package:")]
        found = [p for p in packages if wanted and wanted in re.sub(r"[^a-z0-9]", "", p.lower())]
        if not found:
            raise ValueError(f"No app like '{name}' on {device}. Use action apps to see the installed ones.")
        return min(found, key=len)

    def elements(device: str) -> list[dict]:
        command = "uiautomator dump /sdcard/jarvis-screen.xml >/dev/null && cat /sdcard/jarvis-screen.xml"
        try:
            xml = shell(device, command)
        except RuntimeError:  # "could not get idle state" while something animates: once more
            pause(1)
            xml = shell(device, command)
        return screen_elements(xml)

    def has_keyboard(device: str) -> bool:
        return KEYBOARD.split("/")[0] in shell(device, "pm list packages com.android.adbkeyboard")

    def type_text(device: str, text: str) -> str:
        if text.isascii():
            shell(device, "input text " + shlex.quote(text.replace(" ", "%s")))
            return "Typed."
        if not has_keyboard(device):
            raise RuntimeError("Typing Cyrillic and other letters needs the free ADB Keyboard on the phone. "
                               "Install it once with action install_keyboard, then type again.")
        previous = shell(device, "settings get secure default_input_method").strip()
        shell(device, f"ime enable {KEYBOARD} >/dev/null; ime set {KEYBOARD}")
        try:
            pause(0.6)  # the new keyboard has to attach to the text field first
            shell(device, "am broadcast -a ADB_INPUT_B64 --es msg " + base64.b64encode(text.encode()).decode())
            pause(0.4)
        finally:
            if previous and previous not in ("null", KEYBOARD):
                shell(device, f"ime set {shlex.quote(previous)}")  # the owner's own keyboard comes back
        return "Typed."

    def install_keyboard(device: str) -> str:
        if has_keyboard(device):
            return "ADB Keyboard is already installed."
        apk = Path(ctx.settings.home) / "ADBKeyboard.apk"
        if not apk.exists():
            apk.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(KEYBOARD_APK, timeout=60) as resp:
                apk.write_bytes(resp.read())
        try:
            adb(serial_of(device), "install", "-r", str(apk), timeout=120)
        except RuntimeError as exc:
            hint = (" On Xiaomi phones first turn on Settings > Additional settings > Developer options > "
                    "Install via USB.") if "USER_RESTRICTED" in str(exc) or "INSTALL_FAILED" in str(exc) else ""
            raise RuntimeError(f"Could not install ADB Keyboard: {exc}.{hint}") from exc
        shell(device, f"ime enable {KEYBOARD}")
        return "ADB Keyboard installed. Jarvis can now type in Bulgarian on the phone; your own keyboard stays the default."

    def local_path(path: str) -> Path:
        from .system import allowed_path

        return allowed_path(path, ctx.settings.allowed_roots)

    @registry.tool("List the Android phones and TVs Jarvis can control.", obj({}))
    def android_devices():
        return devices_from_env() or "None connected. Plug the phone in by USB or pair it with android_wireless."

    @registry.tool(
        "Control an Android phone or Android/Google TV fully, like the owner's own hands. Actions: "
        "read_screen (everything on the screen as text with tap coordinates: faster and cheaper than look) · "
        "tap_text (tap the button/item whose label is text) · look (screenshot you can see) · tap (x, y) · "
        "swipe (x, y, x2, y2) · type (text, any language incl. Cyrillic, into the focused field) · key (name: "
        + ", ".join(KEYS) + ") · open_app (app name or package) · apps (installed apps) · open_url · "
        "status (battery, screen, current app, call state) · notifications · sms (recent text messages; text "
        "filters by number or words) · calls (recent calls) · contacts (phone contacts; text filters) · "
        "files (list a folder, default the camera photos) · pull (copy path from the phone to the computer, "
        "to = where) · push (copy path from the computer to the phone, to = phone folder) · mirror (show and "
        "control the phone's screen in a window on this computer) · install_keyboard (one-time, for typing Cyrillic).",
        obj({
            "device": ("string", "Device name from android_devices, e.g. 'phone' or 'tv'"),
            "action": ("string", "read_screen | tap_text | look | tap | swipe | type | key | open_app | apps | open_url | "
                                 "status | notifications | sms | calls | contacts | files | pull | push | mirror | install_keyboard"),
            "name?": ("string", "Key name or app name/package"),
            "url?": ("string", "URL for open_url (YouTube links open in the YouTube app)"),
            "text?": ("string", "Text to type, label for tap_text, or a filter for sms/contacts/calls"),
            "x?": ("integer", "X (screen pixels)"), "y?": ("integer", "Y"),
            "x2?": ("integer", "Swipe end X"), "y2?": ("integer", "Swipe end Y"),
            "path?": ("string", "Phone path for files/pull (e.g. /sdcard/DCIM/Camera/IMG_1.jpg), computer path for push"),
            "to?": ("string", "Destination for pull (computer folder or file) or push (phone folder)"),
            "count?": ("integer", "How many sms/calls/files to return (default 15)"),
        }),
    )
    def android(device: str, action: str, name: str | None = None, url: str | None = None, text: str | None = None,
                x: int | None = None, y: int | None = None, x2: int | None = None, y2: int | None = None,
                path: str | None = None, to: str | None = None, count: int = 15):
        count = max(1, min(int(count or 15), 100))
        if action == "read_screen":
            return describe_elements(elements(device))
        if action == "tap_text":
            wanted = (text or name or "").strip().casefold()
            items = elements(device)
            exact = [e for e in items if e["label"].casefold() == wanted]
            similar = exact or [e for e in items if wanted and wanted in e["label"].casefold()]
            if not similar:
                raise ValueError(f'Nothing labelled "{text}" on the screen. Use read_screen to see what is there.')
            hit = sorted(similar, key=lambda e: (not e["tap"], len(e["label"])))[0]
            shell(device, f"input tap {hit['x']} {hit['y']}")
            return f'Tapped "{hit["label"]}" at {hit["x"]},{hit["y"]}. Read the screen again to check.'
        if action == "key":
            shell(device, f"input keyevent {KEYS.get(name or '', name)}")
            return f"Pressed {name}."
        if action == "open_app":
            target = find_app(device, name or "")
            if target.startswith("android."):
                shell(device, f"am start -a {target}")
            else:
                shell(device, f"monkey -p {shlex.quote(target)} -c android.intent.category.LAUNCHER 1")
            return f"Opened {name} ({target})."
        if action == "apps":
            packages = sorted(line.partition(":")[2].strip() for line in shell(device, "pm list packages -3").splitlines()
                              if line.startswith("package:"))
            return "Installed apps (packages): " + ", ".join(packages)
        if action == "open_url":
            shell(device, f"am start -a android.intent.action.VIEW -d {shlex.quote(url or '')}")
            return f"Opened {url}."
        if action == "type":
            return type_text(device, text or "")
        if action == "tap":
            shell(device, f"input tap {x} {y}")
            return "Tapped. Read the screen to check."
        if action == "swipe":
            shell(device, f"input swipe {x} {y} {x2} {y2} 300")
            return "Swiped."
        if action == "look":
            png = adb(serial_of(device), "exec-out", "screencap", "-p", binary=True)
            return Image(png, "image/png", f"Screen of {device} (tap coordinates are in this image's pixels)")
        if action == "status":
            battery = shell(device, "dumpsys battery | grep -E 'level|status|powered'")
            power = shell(device, "dumpsys power | grep -m1 -E 'mWakefulness|Display Power'")
            focus = shell(device, "dumpsys window | grep -m1 -E 'mCurrentFocus|mFocusedApp'")
            call = shell(device, "dumpsys telephony.registry | grep -m1 mCallState")
            return {"battery": battery.strip(), "power": power.strip(), "app": focus.strip(), "call_state (0 idle,1 ringing,2 in call)": call.strip()}
        if action == "notifications":
            out = shell(device, "dumpsys notification --noredact | grep -E 'android.title=|android.text='")
            return out[-6000:] or "No notifications."
        if action in ("sms", "calls", "contacts"):
            return phone_data(device, action, (text or "").strip().casefold(), count)
        if action == "files":
            folder = path or "/sdcard/DCIM/Camera"
            out = shell(device, f"ls -t -p {shlex.quote(folder)} | head -n {count}")
            return f"Newest first in {folder}:\n{out.strip() or '(empty)'}"
        if action == "pull":
            if not path:
                raise ValueError("Give path: the file or folder on the phone.")
            target = local_path(to or str(Path.home() / "Downloads"))
            adb(serial_of(device), "pull", path, str(target), timeout=900)
            return f"Copied {path} to {target}."
        if action == "push":
            if not path:
                raise ValueError("Give path: the file or folder on this computer.")
            source = local_path(path)
            adb(serial_of(device), "push", str(source), to or "/sdcard/Download/", timeout=900)
            return f"Copied {source} to {to or '/sdcard/Download/'} on the phone."
        if action == "mirror":
            scrcpy = shutil.which("scrcpy")
            if not scrcpy:
                raise NotConfigured("Phone screen on the computer", [
                    "install scrcpy (free): on Windows run `winget install Genymobile.scrcpy`, then try again"])
            subprocess.Popen([scrcpy, "-s", serial_of(device), "--window-title", f"Jarvis · {device}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            return f"The {device} screen is open in a window; it can be used with the mouse and keyboard."
        if action == "install_keyboard":
            return install_keyboard(device)
        raise ValueError(f"Unknown action {action}")

    def phone_data(device: str, kind: str, wanted: str, count: int) -> str:
        if kind == "sms":
            keys = ["address", "body", "date", "type"]
            uri = "content://sms"
        elif kind == "calls":
            keys = ["number", "name", "type", "date", "duration"]
            uri = "content://call_log/calls"
        else:
            keys = ["display_name", "data1"]
            uri = "content://com.android.contacts/data/phones"
        command = f"content query --uri {uri} --projection {':'.join(keys)}"
        if kind != "contacts":
            command += " --sort " + shlex.quote("date DESC")
        try:
            rows = content_rows(shell(device, command, timeout=60), keys)
        except RuntimeError as exc:
            raise RuntimeError(f"The phone did not allow reading {kind} ({exc}). Open the app (open_app) and "
                               "use read_screen instead.") from exc
        if wanted:
            rows = [r for r in rows if any(wanted in (v or "").casefold() for v in r.values())]
        if kind == "sms":
            lines = [f"{when(r.get('date'))} {'from' if r.get('type') == '1' else 'to'} {r.get('address')}: {r.get('body')}"
                     for r in rows[:count]]
        elif kind == "calls":
            kinds = {"1": "incoming", "2": "outgoing", "3": "missed", "4": "voicemail", "5": "rejected", "6": "blocked"}
            lines = [f"{when(r.get('date'))} {kinds.get(r.get('type') or '', 'call')} {r.get('name') or ''} "
                     f"{r.get('number')} ({r.get('duration') or 0} s)" for r in rows[:count]]
        else:
            lines = sorted({f"{r.get('display_name')}: {r.get('data1')}" for r in rows})[:max(count, 50)]
        return "\n".join(lines) or f"No {kind} found."

    @registry.tool(
        "Connect the owner's Android phone (or TV) to Jarvis over Wi-Fi, without a cable. "
        "pair: once, on the phone open Settings > Developer options > Wireless debugging > Pair device with "
        "pairing code and give the IP address:port and the 6-digit code shown there; after that the phone "
        "connects by itself whenever Wireless debugging is on. usb_to_wifi: when the phone is plugged in by "
        "USB, switch it to Wi-Fi so the cable can come out (until the phone restarts). status: what is connected.",
        obj({
            "action": ("string", "pair | usb_to_wifi | status"),
            "address?": ("string", "For pair: IP address and port from the pairing window, e.g. 192.168.1.20:37099"),
            "code?": ("string", "For pair: the 6-digit Wi-Fi pairing code"),
        }),
    )
    def android_wireless(action: str, address: str | None = None, code: str | None = None):
        if not shutil.which("adb"):
            raise NotConfigured("Android control", ["install Android platform-tools (adb)"])
        before = set(connected())
        if action == "status":
            return devices_from_env() or "Nothing connected."
        if action == "pair":
            if not address or not code:
                raise ValueError("Give the address (IP:port) and the 6-digit code from the phone's pairing window.")
            done, out, _ = run_capture(["adb", "pair", address.strip(), code.strip()], timeout=40)
            text = out.decode(errors="replace").strip()
            if done != 0 or "success" not in text.lower():
                raise RuntimeError(f"Pairing failed: {text or 'no answer'}. Check the code and that the phone is on the same Wi-Fi.")
            host = address.strip().rsplit(":", 1)[0]
            for _ in range(15):  # adb finds the paired phone on the network by itself
                new = [s for s in connected() if s not in before or host in s]
                if new:
                    return f"Paired and connected ({new[0]}). The phone now works without a cable."
                pause(1)
            return ("Paired. If the phone does not show up in a few seconds, give its IP address:port from the "
                    "Wireless debugging screen (not the pairing window) to pair again.")
        if action == "usb_to_wifi":
            usb = [s for s in before if ":" not in s and WIRELESS not in s]
            if not usb:
                raise RuntimeError("No phone is plugged in by USB.")
            info = adb(usb[0], "shell", "ip -f inet addr show wlan0")
            found = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", info)
            if not found:
                raise RuntimeError("The phone is not on Wi-Fi. Connect it to the same Wi-Fi as this computer.")
            adb(usb[0], "tcpip", "5555")
            pause(2)
            serial = f"{found[1]}:5555"
            run_capture(["adb", "connect", serial], timeout=15)
            if serial not in connected():
                raise RuntimeError(f"Could not reach the phone at {serial} over Wi-Fi.")
            others = {k: v for k, v in configured_devices().items() if k != "phone" and v not in (usb[0], serial)}
            save_settings(ctx, {"JARVIS_ADB_DEVICES": ",".join(f"{k}={v}" for k, v in {"phone": serial, **others}.items())})
            return f"The phone now works over Wi-Fi at {serial}; the cable can come out. After a restart of the phone, plug it in once more or pair it."
        raise ValueError(f"Unknown action {action}")

    @registry.tool(
        "Call someone from the owner's own mobile number (through the Android phone).",
        obj({"device": ("string", "Phone device name"), "number": ("string", "Phone number")}),
        confirm=True,
        summarize=lambda a: f"ОБАЖДАНЕ от твоя телефон до {a.get('number')}",
    )
    def phone_call(device: str, number: str):
        shell(device, f"am start -a android.intent.action.CALL -d tel:{shlex.quote(number)}")
        return f"Calling {number} from the phone."

    @registry.tool(
        "Answer the ringing call on the owner's phone, or hang up the current call.",
        obj({"device": ("string", "Phone device name"), "action": ("string", "answer | hang_up")}),
        confirm=True,
        summarize=lambda a: "ВДИГНИ телефона" if a.get("action") == "answer" else "ЗАТВОРИ разговора",
    )
    def phone_answer(device: str, action: str):
        shell(device, "input keyevent " + ("KEYCODE_CALL" if action == "answer" else "KEYCODE_ENDCALL"))
        return "Answered." if action == "answer" else "Hung up."

    @registry.tool(
        "Send an SMS from the owner's own mobile number (through the Android phone). Opens the message "
        "pre-filled; then tap Send (android tap_text, or read_screen to find it).",
        obj({"device": ("string", "Phone device name"), "number": ("string", "Recipient"), "text": ("string", "Message")}),
        confirm=True,
        summarize=lambda a: f"SMS от твоя телефон до {a.get('number')}: „{a.get('text')}“",
    )
    def phone_sms(device: str, number: str, text: str):
        shell(device, "am start -a android.intent.action.SENDTO -d " + shlex.quote(f"sms:{number}")
              + " --es sms_body " + shlex.quote(text) + " --ez exit_on_sent true")
        return "Message composed. Tap Send: android tap_text with the Send button's label, or read_screen to find it."
