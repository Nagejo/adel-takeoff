"""Shared identity and numeric rules for the v1 takeoff tool."""

from datetime import date
from decimal import Decimal

CONTRACTOR = "Adel Electric, LLC"
DISCLAIMER = (
    "This quantity takeoff is not a permit set and is not engineered design."
)

# Labor rates are each their own line. Journeyman starts at this default and
# the UI and export warn until the estimator changes it.
JOURNEYMAN_DEFAULT = Decimal("115")
FOREMAN_DEFAULT = Decimal("130")
PM_DEFAULT = Decimal("150")
APPRENTICE_DEFAULT = Decimal("65")

LABOR_RATES = (
    ("journeyman", "Journeyman", JOURNEYMAN_DEFAULT),
    ("foreman", "Foreman", FOREMAN_DEFAULT),
    ("pm", "PM or master", PM_DEFAULT),
    ("apprentice", "Apprentice", APPRENTICE_DEFAULT),
)

JOURNEYMAN_WARNING = (
    "Warning: the journeyman labor rate is the default $115/hr and was left "
    "in place. The estimator should confirm it."
)

# Permits issued after this date use the next Florida code cycle.
PERMIT_CODE_CUTOFF = date(2026, 12, 31)

# Markup is a job decimal. 0.35 means 35 percent. Blank means do not apply.
MAX_MARKUP = Decimal("5")

# A panel or circuit tag must sit this close to the device. Farther text is
# not a link.
LINK_RADIUS_PT = 48.0

# Legend rows farther apart than this are not the same legend block.
LEGEND_ROW_GAP_PT = 72.0

# Symbol clusters larger than this are sheet geometry, not a device mark.
MAX_SYMBOL_PT = 42.0
MIN_SYMBOL_PT = 4.0

# Match lines are long strokes, not device marks.
MIN_MATCH_LINE_PT = 80.0
