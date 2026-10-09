"""Smart home through Home Assistant, which speaks to almost every brand (Philips Hue, Xiaomi,
Tuya/Smart Life, Sonoff, Shelly, Samsung, LG, Daikin, Gree, TP-Link, Google/Nest, Ring, ...).

Set HOME_ASSISTANT_URL (e.g. http://homeassistant.local:8123) and HOME_ASSISTANT_TOKEN
(Profile > Security > Long-lived access tokens).
"""

from __future__ import annotations

import json
import os
import urllib.request

from ..tools import Image, ToolRegistry, obj
from . import NotConfigured

SECURITY_DOMAINS = {"lock", "alarm_control_panel"}


def ha(path: str, body: dict | None = None, raw: bool = False):
    url = os.environ.get("HOME_ASSISTANT_URL", "").rstrip("/")
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
    if not (url and token):
        raise NotConfigured("Smart home (Home Assistant)", ["HOME_ASSISTANT_URL", "HOME_ASSISTANT_TOKEN"])
    req = urllib.request.Request(
        f"{url}/api/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data if raw else json.loads(data or b"null")


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "List smart-home devices and their state, optionally filtered by area/name/type keyword "
        "(e.g. 'light', 'kitchen', 'climate', 'camera', 'хол').",
        obj({"filter?": ("string", "Keyword matched against entity id and friendly name")}),
    )
    def home_devices(filter: str = ""):
        needle = filter.casefold()
        out = []
        for st in ha("states"):
            name = st["attributes"].get("friendly_name", "")
            if needle and needle not in st["entity_id"].casefold() and needle not in name.casefold():
                continue
            attrs = {k: v for k, v in st["attributes"].items()
                     if k in ("brightness", "temperature", "current_temperature", "hvac_mode", "volume_level",
                              "media_title", "unit_of_measurement", "battery_level", "rgb_color")}
            out.append({"entity_id": st["entity_id"], "name": name, "state": st["state"], **attrs})
        return out[:300]

    @registry.tool(
        "Control smart-home devices with a Home Assistant service, e.g. light.turn_on "
        "(data: brightness_pct, rgb_color, color_temp_kelvin), light.turn_off, switch.toggle, "
        "climate.set_temperature (temperature, hvac_mode), cover.open_cover, media_player.play_media, "
        "media_player.volume_set, vacuum.start, scene.turn_on, script.turn_on, fan.set_percentage.",
        obj({
            "service": ("string", "domain.service, e.g. light.turn_on"),
            "entity_id?": ("string", "Target entity, e.g. light.living_room (comma-separate several)"),
            "data_json?": ("string", "Extra service data as JSON, e.g. {\"brightness_pct\": 40}"),
        }),
    )
    def home_control(service: str, entity_id: str | None = None, data_json: str | None = None):
        domain, _, action = service.partition(".")
        if domain in SECURITY_DOMAINS:
            raise PermissionError("Locks and alarms go through home_security, which asks the owner first.")
        return _call(domain, action, entity_id, data_json)

    @registry.tool(
        "Lock/unlock doors or arm/disarm the alarm (lock.lock, lock.unlock, lock.open, "
        "alarm_control_panel.alarm_arm_away, alarm_control_panel.alarm_disarm ...).",
        obj({
            "service": ("string", "domain.service"),
            "entity_id": ("string", "Lock or alarm entity"),
            "data_json?": ("string", "Extra data as JSON, e.g. {\"code\": \"1234\"}"),
        }),
        confirm=True,
        summarize=lambda a: f"ДОМ/СИГУРНОСТ: {a.get('service')} на {a.get('entity_id')}",
    )
    def home_security(service: str, entity_id: str, data_json: str | None = None):
        domain, _, action = service.partition(".")
        if domain not in SECURITY_DOMAINS:
            raise ValueError("Use home_control for non-security devices.")
        return _call(domain, action, entity_id, data_json)

    @registry.tool("See a picture from a security camera.", obj({"entity_id": ("string", "camera.* entity")}))
    def home_camera(entity_id: str):
        return Image(ha(f"camera_proxy/{entity_id}", raw=True), "image/jpeg", f"Camera {entity_id}")

    def _call(domain: str, action: str, entity_id: str | None, data_json: str | None):
        data = json.loads(data_json) if data_json else {}
        if entity_id:
            data["entity_id"] = [e.strip() for e in entity_id.split(",")] if "," in entity_id else entity_id
        changed = ha(f"services/{domain}/{action}", data)
        return {"done": f"{domain}.{action}", "changed": [
            {"entity_id": s["entity_id"], "state": s["state"]} for s in (changed or [])
        ]}
