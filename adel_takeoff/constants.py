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

# Starter assembly quantities and labor hours are examples. They are not a
# published labor standard, and they are not priced until the estimator
# confirms that assembly.
UNCONFIRMED_ASSEMBLY_WARNING = (
    "Unconfirmed assembly: starter per-unit quantities and labor hours are "
    "examples. They are not a published labor standard. Raceway and conductor "
    "lengths are allowances, not measured lengths. Example hours are not in "
    "direct cost until the estimator confirms the assembly."
)
CONFIRMED_ASSEMBLY_NOTE = (
    "Assemblies on counted lines were confirmed by the estimator. "
    "They are not a published labor standard. "
    "Raceway and conductor lengths are allowances, not measured lengths."
)

# Permits issued after this date use the next Florida code cycle.
PERMIT_CODE_CUTOFF = date(2026, 12, 31)

# Markup is a job decimal. 0.35 means 35 percent. Blank means do not apply.
MAX_MARKUP = Decimal("5")

# 72 PDF points are one inch on the sheet.
POINTS_PER_INCH = Decimal("72")

# A printed scale and a graphic bar within this relative difference confirm
# each other. A wider gap is a refusal. The two readings are not averaged.
SCALE_AGREEMENT_TOLERANCE = Decimal("0.05")

# A panel or circuit tag must sit this close to the device. Farther text is
# not a link.
LINK_RADIUS_PT = 48.0

# Legend rows farther apart than this are not the same legend block.
LEGEND_ROW_GAP_PT = 72.0

# Symbol clusters larger than this are sheet geometry, not a device mark.
MAX_SYMBOL_PT = 42.0
MIN_SYMBOL_PT = 4.0

# Exact legend fingerprint equality. The detector records this when every
# geometry component matches. It is not a probability, and it is not recorded
# when nothing matched.
EXACT_MATCH_SCORE = 1.0

# Bands apply only to a score the detector recorded. A missing score stays
# blank. high: score >= high_min. check: score >= check_min. low: below check_min.
CONFIDENCE_HIGH_MIN = 0.90
CONFIDENCE_CHECK_MIN = 0.60

# Match lines are long strokes, not device marks.
MIN_MATCH_LINE_PT = 80.0

# A schedule header is the column line within this distance of the heading.
# Data rows stay in the table while each next line starts within the row gap.
# A larger gap is the next schedule, not another row.
SCHEDULE_HEADER_WINDOW_PT = 56.0
SCHEDULE_ROW_GAP_PT = 30.0

# Words in one column title sit close together. A wider gap is the next column,
# so MOUNTING and HEIGHT stay separate when the drawing gives them two columns.
SCHEDULE_HEADER_PHRASE_GAP_PT = 24.0

# A discrepancy lowers a recorded confidence band to this cap. It does not
# create a band when the detector recorded no score. low stays low.
DISCREPANCY_CONFIDENCE_CAP = "check"

# Scanned pages are rendered at this resolution for Tesseract. OCR words on
# one printed line can sit a few points apart, so line grouping is looser
# than the vector-text grouping.
OCR_DPI = 200
OCR_PSM = 6
OCR_LINE_TOLERANCE_PT = 8.0

# Revision alignment searches a translation of this many points. A sheet
# that needs a larger shift, or that agrees below the minimum, is refused.
ALIGNMENT_WINDOW_PT = 36
ALIGNMENT_MIN_AGREEMENT = 0.72
ALIGNMENT_ASPECT_TOLERANCE = 0.03

# After that translation, the same label within the persist distance keeps
# the old item id. The same panel, circuit, and a smaller move is unchanged.
PERSIST_DISTANCE_PT = 18.0
UNCHANGED_MOVE_PT = 6.0
