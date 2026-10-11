from __future__ import annotations

import base64
import json
import sys
from types import SimpleNamespace

import pytest

from jarvis.plugins import android, daily

SCREEN = """UI hierchary dumped to: /sdcard/jarvis-screen.xml
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node index="0" text="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,2400]">
<node index="1" text="Съобщение" resource-id="com.app:id/compose" class="android.widget.EditText" clickable="true" bounds="[40,2100][900,2200]" />
<node index="2" text="" content-desc="Изпрати" resource-id="com.app:id/send" class="android.widget.ImageButton" clickable="true" bounds="[920,2100][1060,2200]" />
<node index="3" text="Wi-Fi" class="android.widget.Switch" checkable="true" checked="true" clickable="true" bounds="[0,300][1080,400]" />
<node index="4" text="" resource-id="com.app:id/spacer" class="android.view.View" clickable="false" bounds="[0,0][0,0]" />
</node></hierarchy>"""


class Phone:
    """Stands in for adb: answers shell commands and records every call."""

    def __init__(self, answers=None, devices="List of devices attached\nUSB123\tdevice\n"):
        self.answers = answers or {}
        self.devices = devices
        self.calls = []

    def __call__(self, args, timeout=30, merge=True, **_):
        self.calls.append(args)
        if args[:2] == ["adb", "devices"]:
            return 0, self.devices.encode(), b""
        command = " ".join(args[3:]) if args[:1] == ["adb"] and args[1] == "-s" else " ".join(args[1:])
        for key, value in self.answers.items():
            if key in command:
                return (0, value.encode(), b"") if isinstance(value, str) else value
        return 0, b"", b""

    def shell(self):
        return [" ".join(c[4:]) for c in self.calls if c[:2] == ["adb", "-s"] and c[3] == "shell"]


@pytest.fixture
def phone(monkeypatch):
    def install(**kwargs):
        fake = Phone(**kwargs)
        monkeypatch.setattr(android.shutil, "which", lambda name: name)
        monkeypatch.setattr(android, "run_capture", fake)
        monkeypatch.setattr(android, "pause", lambda s: None)
        monkeypatch.delenv("JARVIS_ADB_DEVICES", raising=False)
        return fake
    return install


def run(registry, tool, **args):
    output, is_error = registry.run(tool, args, lambda s: True)
    return output, is_error


def test_the_phone_screen_is_read_as_text_and_buttons_are_tapped_by_name(registry, phone):
    fake = phone(answers={"uiautomator dump": SCREEN})
    screen, is_error = run(registry, "android", device="phone", action="read_screen")
    assert not is_error
    assert '"Съобщение" EditText at 470,2150, text field, tappable' in screen
    assert '"Изпрати" ImageButton at 990,2150, tappable' in screen
    assert '"Wi-Fi" Switch at 540,350, on, tappable' in screen and "spacer" not in screen
    out, is_error = run(registry, "android", device="phone", action="tap_text", text="изпрати")
    assert not is_error and "Изпрати" in out
    assert fake.shell()[-1] == "input tap 990 2150"


def test_cyrillic_is_typed_with_the_adb_keyboard_and_the_owners_keyboard_comes_back(registry, phone):
    own = "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
    fake = phone(answers={"pm list packages com.android.adbkeyboard": "package:com.android.adbkeyboard\n",
                          "default_input_method": own + "\n"})
    out, is_error = run(registry, "android", device="phone", action="type", text="Здравей, мамо!")
    assert not is_error and out == "Typed."
    sent = [c for c in fake.shell() if "ADB_INPUT_B64" in c][0]
    assert base64.b64decode(sent.split()[-1]).decode() == "Здравей, мамо!"
    assert fake.shell()[-1] == f"ime set {own}"
    run(registry, "android", device="phone", action="type", text="hello world")
    assert fake.shell()[-1] == "input text hello%sworld"


def test_without_the_keyboard_jarvis_is_told_how_to_type_cyrillic(registry, phone):
    phone(answers={"pm list packages com.android.adbkeyboard": ""})
    out, is_error = run(registry, "android", device="phone", action="type", text="Здравей")
    assert is_error and "install_keyboard" in out


def test_messages_calls_and_contacts_are_read_from_the_phone(registry, phone):
    sms = ("Row: 0 address=+359888111222, body=Здрасти, ще закъснея\nс 10 минути, date=1791400000000, type=1\n"
           "Row: 1 address=+359877000000, body=Добре, date=1791300000000, type=2\n")
    calls = "Row: 0 number=+359888111222, name=Мама, type=3, date=1791400000000, duration=0\n"
    contacts = "Row: 0 display_name=Мама, data1=+359 888 111 222\nRow: 1 display_name=Иван, data1=0899 123 456\n"
    phone(answers={"content://sms": sms, "content://call_log": calls, "content://com.android.contacts": contacts})
    out, _ = run(registry, "android", device="phone", action="sms")
    assert "from +359888111222: Здрасти, ще закъснея\nс 10 минути" in out and "to +359877000000: Добре" in out
    out, _ = run(registry, "android", device="phone", action="sms", text="закъснея")
    assert "Добре" not in out
    out, _ = run(registry, "android", device="phone", action="calls")
    assert "missed Мама +359888111222" in out
    out, _ = run(registry, "android", device="phone", action="contacts", text="иван")
    assert out == "Иван: 0899 123 456"


def test_apps_open_by_their_everyday_name(registry, phone):
    fake = phone(answers={"pm list packages": "package:com.android.chrome\npackage:com.revolut.revolut\n"})
    out, is_error = run(registry, "android", device="phone", action="open_app", name="Revolut")
    assert not is_error and "com.revolut.revolut" in out
    assert "monkey -p com.revolut.revolut" in fake.shell()[-1]
    out, is_error = run(registry, "android", device="phone", action="open_app", name="Settings")
    assert fake.shell()[-1] == "am start -a android.settings.SETTINGS"


def test_photos_come_from_the_phone_to_the_computer(registry, phone, tmp_path):
    fake = phone()
    out, is_error = run(registry, "android", device="phone", action="pull", path="/sdcard/DCIM/Camera/IMG_1.jpg",
                        to=str(tmp_path))
    assert not is_error
    assert ["adb", "-s", "USB123", "pull", "/sdcard/DCIM/Camera/IMG_1.jpg", str(tmp_path)] in fake.calls
    out, is_error = run(registry, "android", device="phone", action="pull", path="/sdcard/x.jpg", to="/etc")
    assert is_error and "allowed folders" in out


def test_phones_found_by_adb_join_the_named_ones(phone, monkeypatch):
    phone(devices="List of devices attached\n192.168.1.30:5555\tdevice\nadb-AB12-x._adb-tls-connect._tcp\tdevice\n")
    monkeypatch.setenv("JARVIS_ADB_DEVICES", "tv=192.168.1.30:5555")
    assert android.devices_from_env() == {"tv": "192.168.1.30:5555", "phone": "adb-AB12-x._adb-tls-connect._tcp"}


def test_pairing_over_wifi_once_connects_the_phone_without_a_cable(registry, phone):
    fake = phone(devices="List of devices attached\n", answers={"pair 192.168.1.20:37099 123456": "Successfully paired"})

    def found(args, **kwargs):
        if args[:2] == ["adb", "pair"]:
            fake.devices += "adb-AB12-x._adb-tls-connect._tcp\tdevice\n"
        return Phone.__call__(fake, args, **kwargs)

    android.run_capture = found
    out, is_error = run(registry, "android_wireless", action="pair", address="192.168.1.20:37099", code="123456")
    assert not is_error and "adb-AB12-x._adb-tls-connect._tcp" in out


def test_usb_phone_moves_to_wifi_and_is_remembered(registry, phone, ctx, monkeypatch):
    saved = {}
    monkeypatch.setattr(android, "save_settings", lambda _ctx, values: saved.update(values))
    fake = phone(answers={"ip -f inet addr show wlan0": "inet 192.168.1.20/24 brd 192.168.1.255 scope global wlan0"})

    def connect(args, **kwargs):
        if args[:2] == ["adb", "connect"]:
            fake.devices += "192.168.1.20:5555\tdevice\n"
        return Phone.__call__(fake, args, **kwargs)

    android.run_capture = connect
    out, is_error = run(registry, "android_wireless", action="usb_to_wifi")
    assert not is_error and saved == {"JARVIS_ADB_DEVICES": "phone=192.168.1.20:5555"}
    assert ["adb", "-s", "USB123", "tcpip", "5555"] in fake.calls


def test_calls_and_sms_from_the_phone_still_ask_every_time(registry, phone):
    phone()
    asked = []
    out, is_error = registry.run("phone_sms", {"device": "phone", "number": "+359888111222", "text": "Идвам"},
                                 lambda s: asked.append(s) or False, trust_local=True)
    assert asked and "Идвам" in asked[0] and is_error


def test_weather_is_told_in_bulgarian(monkeypatch):
    def fake(url, timeout=10, cookie=None):
        if "geocoding" in url:
            assert "name=%D0%9F%D0%BB%D0%BE%D0%B2%D0%B4%D0%B8%D0%B2" in url
            return json.dumps({"results": [{"name": "Пловдив", "latitude": 42.15, "longitude": 24.75}]})
        return json.dumps({
            "current": {"temperature_2m": 12.4, "apparent_temperature": 10.6, "relative_humidity_2m": 60,
                        "weather_code": 0, "wind_speed_10m": 8.2},
            "daily": {"time": ["2026-10-08", "2026-10-09"], "weather_code": [3, 61], "temperature_2m_min": [6.1, 7],
                      "temperature_2m_max": [15.5, 13], "precipitation_probability_max": [10, 80]},
        })

    monkeypatch.setattr(daily, "fetch", fake)
    report = daily.weather_report("Пловдив", days=2)
    assert report.splitlines() == [
        "Пловдив: сега 12°C (усеща се като 11°C), ясно, вятър 8 км/ч, влажност 60%.",
        "Днес: 6…16°C, облачно, вероятност за валеж 10%.",
        "Утре: 7…13°C, слаб дъжд, вероятност за валеж 80%.",
    ]
    monkeypatch.setattr(daily, "fetch", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert daily.short_weather() == ""  # the greeting goes on without it


def test_youtube_plays_the_first_match(registry, monkeypatch):
    page = ('..."videoRenderer":{"videoId":"pAgnJDJN4VA","thumbnail":{},"title":{"runs":[{"text":"AC/DC - Back In Black '
            '(Official Video)"}]}...')
    opened = []
    monkeypatch.setattr(daily, "fetch", lambda url, timeout=10, cookie=None: page)
    monkeypatch.setattr(daily.webbrowser, "open", opened.append)
    out, is_error = run(registry, "play_youtube", query="Back in Black")
    assert not is_error and "Back In Black" in out
    assert opened == ["https://www.youtube.com/watch?v=pAgnJDJN4VA"]


def test_media_keys_and_locking_work_without_asking(registry, monkeypatch):
    from jarvis.plugins import system

    pressed, ran = [], []
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(press=pressed.append))
    monkeypatch.setattr(system.sys, "platform", "win32")
    monkeypatch.setattr(system, "run_capture", lambda args, **k: ran.append(args) or (0, b"", b""))
    never = lambda s: pytest.fail("should not ask")  # noqa: E731
    assert registry.run("media_control", {"key": "play_pause"}, never) == ("Done.", False)
    registry.run("media_control", {"key": "volume_up", "times": 3}, never)
    assert pressed == ["playpause", "volumeup", "volumeup", "volumeup"]
    registry.run("lock_computer", {"action": "lock"}, never)
    assert ran == [["rundll32.exe", "user32.dll,LockWorkStation"]]
    asked = []
    registry.run("power_off", {"action": "shutdown"}, lambda s: asked.append(s) or False)
    assert asked == ["ИЗКЛЮЧИ компютъра"] and len(ran) == 1


def test_jarvis_watches_a_video_with_gemini(tmp_path):
    pytest.importorskip("google.genai")
    from google.genai import types

    from jarvis import gemini

    seen = []

    class Files:
        def upload(self, file):
            seen.append(("upload", file))
            return types.File(name="files/v1", state="PROCESSING")

        def get(self, name):
            return types.File(name=name, state="ACTIVE")

        def delete(self, name):
            seen.append(("delete", name))

    class Models:
        def generate_content(self, model, contents, config):
            seen.append(("ask", model, contents[0]))
            return types.GenerateContentResponse(candidates=[types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text="00:03 Jarvis казва „Добро утро“.")]))])

    client = SimpleNamespace(files=Files(), models=Models())
    video = tmp_path / "jarvis.mp4"
    video.write_bytes(b"\0")
    assert "Добро утро" in gemini.watch(str(video), client=client, wait=lambda s: None)
    assert seen[0] == ("upload", str(video)) and seen[-1] == ("delete", "files/v1")
    seen.clear()
    gemini.watch("https://www.youtube.com/watch?v=pAgnJDJN4VA", "Какво свири?", client=client)
    assert seen[0][2].file_data.file_uri.endswith("pAgnJDJN4VA") and not any(s[0] == "upload" for s in seen)


def test_jarvis_answers_in_cyrillic_bulgarian(ctx, registry):
    from conftest import FakeClient

    from jarvis.brain import Jarvis

    prompt = Jarvis(ctx.settings, ctx.store, registry, lambda s: True, client=FakeClient([])).system_prompt()
    assert "Bulgarian written in Cyrillic" in prompt and "zashto raboti bavno" in prompt
