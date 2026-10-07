"""Android phones and Android/Google TVs over adb (USB or Wi-Fi).

Configure devices as JARVIS_ADB_DEVICES="phone=192.168.1.20:5555,tv=192.168.1.30:5555"
(enable Developer options > USB / Wireless debugging on each device; first connection asks
to allow this computer). On the phone this gives Jarvis your own mobile number: it can call
from it, answer and hang up, send SMS, and see and tap the screen like a person.
"""

from __future__ import annotations

import os
import shlex
import shutil

from ..procs import run_capture
from ..tools import Image, ToolRegistry, obj
from . import NotConfigured

KEYS = {
    "home": "KEYCODE_HOME", "back": "KEYCODE_BACK", "power": "KEYCODE_POWER", "wake": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP", "volume_up": "KEYCODE_VOLUME_UP", "volume_down": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE", "play_pause": "KEYCODE_MEDIA_PLAY_PAUSE", "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS", "up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT", "right": "KEYCODE_DPAD_RIGHT", "ok": "KEYCODE_DPAD_CENTER",
    "enter": "KEYCODE_ENTER", "answer_call": "KEYCODE_CALL", "end_call": "KEYCODE_ENDCALL",
    "channel_up": "KEYCODE_CHANNEL_UP", "channel_down": "KEYCODE_CHANNEL_DOWN", "input": "KEYCODE_TV_INPUT",
}

APPS = {
    "youtube": "com.google.android.youtube", "youtube_tv": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja", "spotify": "com.spotify.music", "chrome": "com.android.chrome",
    "maps": "com.google.android.apps.maps", "whatsapp": "com.whatsapp", "viber": "com.viber.voip",
    "messenger": "com.facebook.orca", "camera": "android.media.action.STILL_IMAGE_CAMERA",
}


def devices_from_env() -> dict[str, str]:
    spec = os.environ.get("JARVIS_ADB_DEVICES", "")
    out = {}
    for item in filter(None, (p.strip() for p in spec.split(","))):
        name, _, serial = item.partition("=")
        out[name.strip()] = serial.strip()
    return out


def adb(serial: str, *args: str, timeout: float = 30, binary: bool = False):
    if not shutil.which("adb"):
        raise NotConfigured("Android control", ["install Android platform-tools (adb)"])
    if ":" in serial:
        run_capture(["adb", "connect", serial], timeout=15)
    code, out, err = run_capture(["adb", "-s", serial, *args], timeout=timeout, merge=False)
    if code is None:
        raise RuntimeError(f"adb did not answer in {timeout:g} seconds. Is the device on and allowed?")
    if code:
        raise RuntimeError(err.decode(errors="replace").strip() or "adb failed")
    return out if binary else out.decode(errors="replace")


def register(registry: ToolRegistry, ctx) -> None:
    def serial_of(device: str) -> str:
        devices = devices_from_env()
        if not devices:
            raise NotConfigured("Android phone/TV", ["JARVIS_ADB_DEVICES (e.g. phone=192.168.1.20:5555,tv=192.168.1.30:5555)"])
        if device not in devices:
            raise ValueError(f"Unknown device '{device}'. Known: {', '.join(devices)}")
        return devices[device]

    def shell(device: str, command: str) -> str:
        return adb(serial_of(device), "shell", command)

    @registry.tool("List the Android phones and TVs Jarvis can control.", obj({}))
    def android_devices():
        return devices_from_env() or "None configured (JARVIS_ADB_DEVICES)."

    @registry.tool(
        "Control an Android phone or Android/Google TV. Actions: key (name: "
        + ", ".join(KEYS) + ") · open_app (app name or package) · open_url · type (text) · "
        "tap (x, y) · swipe (x, y, x2, y2) · look (screenshot you can see) · status (battery, screen, current app, call state) · "
        "notifications (recent notification texts).",
        obj({
            "device": ("string", "Device name from android_devices, e.g. 'phone' or 'tv'"),
            "action": ("string", "key | open_app | open_url | type | tap | swipe | look | status | notifications"),
            "name?": ("string", "Key name or app name/package"),
            "url?": ("string", "URL for open_url (YouTube links open in the YouTube app)"),
            "text?": ("string", "Text to type"),
            "x?": ("integer", "X (screen pixels)"), "y?": ("integer", "Y"),
            "x2?": ("integer", "Swipe end X"), "y2?": ("integer", "Swipe end Y"),
        }),
    )
    def android(device: str, action: str, name: str | None = None, url: str | None = None, text: str | None = None,
                x: int | None = None, y: int | None = None, x2: int | None = None, y2: int | None = None):
        if action == "key":
            shell(device, f"input keyevent {KEYS.get(name or '', name)}")
            return f"Pressed {name}."
        if action == "open_app":
            target = APPS.get((name or "").lower(), name or "")
            if target.startswith("android."):
                shell(device, f"am start -a {target}")
            else:
                shell(device, f"monkey -p {shlex.quote(target)} -c android.intent.category.LAUNCHER 1")
            return f"Opened {name}."
        if action == "open_url":
            shell(device, f"am start -a android.intent.action.VIEW -d {shlex.quote(url or '')}")
            return f"Opened {url}."
        if action == "type":
            # adb's input text needs spaces as %s; non-ASCII may need an ADB keyboard app on the device.
            shell(device, "input text " + shlex.quote((text or "").replace(" ", "%s")))
            return "Typed."
        if action == "tap":
            shell(device, f"input tap {x} {y}")
            return "Tapped. Look to check."
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
        "pre-filled; then look at the screen and tap Send.",
        obj({"device": ("string", "Phone device name"), "number": ("string", "Recipient"), "text": ("string", "Message")}),
        confirm=True,
        summarize=lambda a: f"SMS от твоя телефон до {a.get('number')}: „{a.get('text')}“",
    )
    def phone_sms(device: str, number: str, text: str):
        shell(device, "am start -a android.intent.action.SENDTO -d " + shlex.quote(f"sms:{number}")
              + " --es sms_body " + shlex.quote(text) + " --ez exit_on_sent true")
        return "Message composed. Use android look, then tap the Send button."
