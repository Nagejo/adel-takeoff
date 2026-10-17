"""Legend-label categories, device types, and job scope.

A category comes from the legend label. A system legend heading can place
the symbol when the label is only an abbreviation. Scope is by EC, by
others, or rough-in only, and each category has a job default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


LANDSCAPE_WORDS = ("landscape",)
CONTROL_PHRASES = (
    "lighting control",
    "lighting controls",
    "light control",
    "control station",
)
POWER_WORDS = (
    "receptacle",
    "outlet",
    "duplex",
    "gfci",
    "gfi",
    "switch",
    "disconnect",
    "junction box",
    "dimmer",
)
LIGHT_WORDS = (
    "light",
    "fixture",
    "luminaire",
    "downlight",
    "sconce",
    "troffer",
    "lamp",
    "exit sign",
)

SCOPE_MODES = ("ec", "by_others", "rough_in")

SCOPE_DEFAULTS = {
    "power": "ec",
    "lighting": "ec",
    "landscape_lighting": "by_others",
    "lighting_control": "by_others",
    "low_voltage": "by_others",
    "fire_alarm": "by_others",
    "controls": "by_others",
}

SCOPE_FIELDS = {
    "power": "scope_power",
    "lighting": "scope_lighting",
    "landscape_lighting": "scope_landscape",
    "lighting_control": "scope_lighting_control",
    "low_voltage": "scope_low_voltage",
    "fire_alarm": "scope_fire_alarm",
    "controls": "scope_controls",
}

# The existing include checkboxes still mean "by EC" for these two.
CHECKBOX_FIELDS = {
    "landscape_lighting": "include_landscape",
    "lighting_control": "include_lighting_control",
}

SYSTEM_NAMES = {
    "power": "power",
    "lighting": "lighting",
    "landscape_lighting": "landscape lighting",
    "lighting_control": "lighting control",
    "low_voltage": "low voltage",
    "fire_alarm": "fire alarm",
    "controls": "controls",
}

BY_OTHERS_PARTY = {
    "landscape_lighting": "by others",
    "lighting_control": "lighting control vendor",
    "controls": "lighting control vendor",
    "low_voltage": "AV/low-voltage integrator",
    "fire_alarm": "FA vendor",
    "power": "EC",
    "lighting": "EC",
}

DEVICE_TYPE_LABELS = {
    "duplex": "Duplex",
    "power": "Power",
    "lighting": "Lighting",
    "landscape": "Landscape lighting",
    "lighting_control": "Lighting control",
    "data": "Data",
    "voice": "Voice",
    "wap": "WAP",
    "tv": "TV",
    "coax": "Coax",
    "av": "AV",
    "speaker": "Speaker",
    "camera": "Camera",
    "intercom": "Intercom",
    "access_control": "Access control",
    "low_voltage": "Low voltage",
    "smoke": "Smoke detector",
    "heat": "Heat detector",
    "co": "CO detector",
    "combo": "Combination detector",
    "horn_strobe": "Horn/strobe",
    "pull_station": "Pull station",
    "duct_detector": "Duct detector",
    "module": "Module",
    "facp": "FACP",
    "annunciator": "Annunciator",
    "fire_alarm": "Fire alarm",
    "keypad": "Keypad",
    "control_dimmer": "Control dimmer",
    "shade": "Shade control",
    "thermostat": "Thermostat",
    "occupancy": "Occupancy sensor",
    "vacancy": "Vacancy sensor",
    "controls": "Controls",
    "other": "Other",
}


@dataclass(frozen=True)
class DeviceClassification:
    category: str
    device_type: str
    system: str


def _text(label: str) -> str:
    return " ".join(str(label or "").lower().split())


def _has(text: str, phrase: str) -> bool:
    return phrase in text


def _token(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def _fire_alarm(text: str) -> str | None:
    if _has(text, "duct detector") or _has(text, "duct smoke"):
        return "duct_detector"
    if _has(text, "combination") or _has(text, "combo") or _has(text, "smoke/co") or _has(text, "smoke co"):
        return "combo"
    if _has(text, "carbon monoxide") or _has(text, "co detector") or _has(text, "co alarm") or _token(text, "co"):
        return "co"
    if _has(text, "smoke") or _token(text, "sd"):
        return "smoke"
    if _has(text, "heat detector") or _has(text, "heat det") or _token(text, "hd"):
        return "heat"
    if _has(text, "horn/strobe") or _has(text, "horn strobe") or _has(text, "strobe") or _has(text, "horn") or _token(text, "hs"):
        return "horn_strobe"
    if _has(text, "pull station") or _token(text, "ps"):
        return "pull_station"
    if (
        _has(text, "monitor module")
        or _has(text, "control module")
        or _has(text, "relay module")
        or _has(text, "isolator module")
        or _has(text, "fa module")
    ):
        return "module"
    if _has(text, "facp") or _has(text, "fire alarm control") or _has(text, "fire alarm panel"):
        return "facp"
    if _has(text, "annunciator"):
        return "annunciator"
    return None


def _low_voltage(text: str) -> str | None:
    if _has(text, "wireless access") or _has(text, "access point") or _token(text, "wap"):
        return "wap"
    if _has(text, "access control") or _has(text, "card reader") or _has(text, "door contact"):
        return "access_control"
    if _has(text, "door station") or _has(text, "intercom"):
        return "intercom"
    if _has(text, "camera") or _has(text, "cctv"):
        return "camera"
    if _has(text, "speaker"):
        return "speaker"
    if _token(text, "coax") or _has(text, "catv"):
        return "coax"
    if _has(text, "television") or _has(text, "tv outlet") or _token(text, "tv"):
        return "tv"
    if _has(text, "audio") or _token(text, "av") or _has(text, "video"):
        return "av"
    if _has(text, "voice") or _has(text, "telephone") or _token(text, "phone"):
        return "voice"
    if _has(text, "data") or _has(text, "low voltage") or _has(text, "lv outlet"):
        return "data"
    return None


def _controls(text: str) -> str | None:
    if _has(text, "occupancy"):
        return "occupancy"
    if _has(text, "vacancy"):
        return "vacancy"
    if _has(text, "keypad"):
        return "keypad"
    if _has(text, "shade"):
        return "shade"
    if _has(text, "thermostat"):
        return "thermostat"
    if _has(text, "dimmer") and any(
        word in text for word in ("control", "keypad", "lutron", "system", "shade")
    ):
        return "control_dimmer"
    return None


def _forced(legend_system: str, text: str) -> DeviceClassification | None:
    if legend_system == "fire_alarm":
        return DeviceClassification("fire_alarm", _fire_alarm(text) or "fire_alarm", SYSTEM_NAMES["fire_alarm"])
    if legend_system == "low_voltage":
        return DeviceClassification("low_voltage", _low_voltage(text) or "low_voltage", SYSTEM_NAMES["low_voltage"])
    if legend_system == "controls":
        return DeviceClassification("controls", _controls(text) or "controls", SYSTEM_NAMES["controls"])
    if legend_system == "lighting_control":
        kind = _controls(text)
        if kind:
            return DeviceClassification("controls", kind, SYSTEM_NAMES["controls"])
        return DeviceClassification(
            "lighting_control",
            "lighting_control",
            SYSTEM_NAMES["lighting_control"],
        )
    return None


def classify_label(label: str, legend_system: str = "") -> DeviceClassification:
    """Map a legend label to a category and device type.

    Low voltage is tested before power so a data outlet is not a receptacle.
    Fire alarm and controls are tested before lighting so a detector is not
    a luminaire. A system legend heading wins when the label is only an
    abbreviation such as SD.
    """

    text = _text(label)
    if any(word in text for word in LANDSCAPE_WORDS) and any(word in text for word in ("light", "fixture", "luminaire", "lamp")):
        return DeviceClassification(
            "landscape_lighting",
            "landscape",
            SYSTEM_NAMES["landscape_lighting"],
        )
    fire = _fire_alarm(text)
    if fire:
        return DeviceClassification("fire_alarm", fire, SYSTEM_NAMES["fire_alarm"])
    low_voltage = _low_voltage(text)
    if low_voltage:
        return DeviceClassification("low_voltage", low_voltage, SYSTEM_NAMES["low_voltage"])
    controls = _controls(text)
    if controls:
        return DeviceClassification("controls", controls, SYSTEM_NAMES["controls"])
    if any(phrase in text for phrase in CONTROL_PHRASES):
        return DeviceClassification(
            "lighting_control",
            "lighting_control",
            SYSTEM_NAMES["lighting_control"],
        )
    forced = _forced(legend_system, text)
    if forced is not None:
        return forced
    if any(word in text for word in POWER_WORDS):
        device = "duplex" if "duplex" in text or "gfci" in text or "gfi" in text else "power"
        return DeviceClassification("power", device, SYSTEM_NAMES["power"])
    if any(word in text for word in LIGHT_WORDS):
        return DeviceClassification("lighting", "lighting", SYSTEM_NAMES["lighting"])
    return DeviceClassification("other", "other", "other")


def categorize(label: str) -> str:
    """Return the category used by the takeoff and the review."""

    return classify_label(label).category


def scope_mode(category: str, job: dict) -> str:
    """Return ec, by_others, rough_in, or not_counted."""

    if category not in SCOPE_DEFAULTS:
        return "not_counted"
    explicit = str(job.get(SCOPE_FIELDS[category]) or "").strip()
    if explicit in SCOPE_MODES:
        return explicit
    checkbox = CHECKBOX_FIELDS.get(category)
    if checkbox and job.get(checkbox):
        return "ec"
    return SCOPE_DEFAULTS[category]


def posted_scope(raw: str, *, checkbox_yes: bool, default: str) -> str:
    """A posted select wins. The older include checkbox means by EC."""

    value = str(raw or "").strip()
    if value in SCOPE_MODES:
        return value
    if checkbox_yes:
        return "ec"
    return default


def counts_for_ec(category: str, job: dict) -> bool:
    """EC and rough-in only stay in the takeoff. By others stays out of EC totals."""

    return scope_mode(category, job) in ("ec", "rough_in")


def in_scope(
    category: str,
    *,
    include_landscape: bool,
    include_lighting_control: bool,
    job: dict | None = None,
) -> bool:
    """Power and lighting are in scope. The other defaults stay by others."""

    source = dict(job or {})
    source.setdefault("include_landscape", include_landscape)
    source.setdefault("include_lighting_control", include_lighting_control)
    return counts_for_ec(category, source)


def scope_label(category: str, counted: bool, mode: str | None = None) -> str:
    if mode == "rough_in" and counted:
        return "rough-in only"
    if category in ("landscape_lighting", "lighting_control", "low_voltage", "fire_alarm", "controls") and not counted:
        return "by others"
    if mode == "by_others" and not counted:
        return "by others"
    if counted:
        return "in scope"
    return "not counted"


def device_type_label(device_type: str) -> str:
    return DEVICE_TYPE_LABELS.get(device_type, device_type or "Other")


def party_for(category: str, mode: str) -> str:
    if mode == "ec":
        return "EC"
    if mode == "rough_in":
        return "EC"
    if mode == "by_others":
        return BY_OTHERS_PARTY.get(category, "by others")
    return ""
