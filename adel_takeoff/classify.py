"""Legend-label categories and job scope. Categories come from label text."""

from __future__ import annotations

LANDSCAPE_WORDS = ("landscape",)
CONTROL_PHRASES = (
    "lighting control",
    "lighting controls",
    "light control",
    "control station",
    "occupancy sensor",
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


def categorize(label: str) -> str:
    """Return power, lighting, landscape_lighting, lighting_control, or other.

    Landscape lighting and lighting control are identified so the job can
    leave them by others. They are not guessed from symbol shape alone.
    """

    text = " ".join(label.lower().split())
    if any(word in text for word in LANDSCAPE_WORDS) and any(
        word in text for word in ("light", "fixture", "luminaire", "lamp")
    ):
        return "landscape_lighting"
    if any(phrase in text for phrase in CONTROL_PHRASES):
        return "lighting_control"
    if any(word in text for word in POWER_WORDS):
        return "power"
    if any(word in text for word in LIGHT_WORDS):
        return "lighting"
    return "other"


def in_scope(
    category: str,
    *,
    include_landscape: bool,
    include_lighting_control: bool,
) -> bool:
    """Power devices and lighting fixtures are in scope.

    Landscape lighting and lighting control stay by others unless this job
    explicitly includes them.
    """

    if category == "power" or category == "lighting":
        return True
    if category == "landscape_lighting":
        return include_landscape
    if category == "lighting_control":
        return include_lighting_control
    return False


def scope_label(category: str, counted: bool) -> str:
    if category in ("landscape_lighting", "lighting_control") and not counted:
        return "by others"
    if counted:
        return "in scope"
    return "not counted"
