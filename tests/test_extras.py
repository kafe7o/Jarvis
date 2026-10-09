from __future__ import annotations

import json

from conftest import Approver

from jarvis.plugins import home, messaging


def test_home_control_and_security_split(registry, monkeypatch):
    calls = []

    def fake_ha(path, body=None, raw=False):
        calls.append((path, body))
        if path == "states":
            return [
                {"entity_id": "light.hol", "state": "off", "attributes": {"friendly_name": "Хол лампа"}},
                {"entity_id": "lock.vhod", "state": "locked", "attributes": {"friendly_name": "Входна врата"}},
            ]
        return [{"entity_id": body["entity_id"], "state": "on"}]

    monkeypatch.setattr(home, "ha", fake_ha)
    out, _ = registry.run("home_devices", {"filter": "хол"}, Approver())
    assert "light.hol" in out and "lock.vhod" not in out
    out, err = registry.run("home_control", {"service": "light.turn_on", "entity_id": "light.hol",
                                             "data_json": "{\"brightness_pct\": 40}"}, Approver())
    assert not err and calls[-1] == ("services/light/turn_on", {"brightness_pct": 40, "entity_id": "light.hol"})
    out, err = registry.run("home_control", {"service": "lock.unlock", "entity_id": "lock.vhod"}, Approver())
    assert err and "home_security" in out
    approver = Approver(False)
    out, err = registry.run("home_security", {"service": "lock.unlock", "entity_id": "lock.vhod"}, approver)
    assert err and "lock.unlock" in approver.asked[0]


def test_whatsapp_cloud_api(registry, monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    sent = []

    class Resp:
        def __init__(self, req):
            sent.append(json.loads(req.data))

        def read(self):
            return b'{"messages":[{"id":"wamid"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(messaging.urllib.request, "urlopen", lambda req, timeout=0: Resp(req))
    registry.run("save_contact", {"name": "Иван", "phone": "+359888999000"}, Approver())
    out, err = registry.run("whatsapp_send", {"to": "иван", "text": "Здрасти"}, Approver())
    assert not err and sent[0]["to"] == "359888999000" and sent[0]["text"]["body"] == "Здрасти"


def test_phone_calls_fall_back_to_twilio_voice_without_public_url(monkeypatch):
    from jarvis.phone_agent import speech_twiml

    monkeypatch.delenv("JARVIS_PUBLIC_URL", raising=False)
    assert speech_twiml("Здравейте", "bg-BG").startswith("<Say")


def test_setup_env_round_trip(tmp_path):
    from jarvis.setup_wizard import read_env, write_env

    path = tmp_path / ".env"
    write_env(path, {"ANTHROPIC_API_KEY": "sk-1", "EMPTY": ""})
    assert read_env(path) == {"ANTHROPIC_API_KEY": "sk-1"}
