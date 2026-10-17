"""Map a detector score to a confidence flag.

The flag is high, check, or low only when a score was recorded. The cutoffs
come from config. A missing score stays blank so a review never shows a
confidence the detector did not produce.
"""

from __future__ import annotations

from dataclasses import dataclass

from adel_takeoff.constants import (
    CONFIDENCE_CHECK_MIN,
    CONFIDENCE_HIGH_MIN,
    DISCREPANCY_CONFIDENCE_CAP,
)


_BAND_RANK = {"low": 0, "check": 1, "high": 2}


@dataclass(frozen=True)
class ConfidenceThresholds:
    high_min: float
    check_min: float

    def __post_init__(self) -> None:
        if not (0 <= self.check_min <= self.high_min <= 1):
            raise ValueError(
                "Confidence thresholds must satisfy 0 <= check_min <= high_min <= 1."
            )

    @classmethod
    def from_config(cls) -> ConfidenceThresholds:
        return cls(high_min=CONFIDENCE_HIGH_MIN, check_min=CONFIDENCE_CHECK_MIN)


def confidence_band(
    score: float | None,
    thresholds: ConfidenceThresholds | None = None,
) -> str:
    """Return high, check, low, or a blank string."""

    if score is None:
        return ""
    limits = thresholds if thresholds is not None else ConfidenceThresholds.from_config()
    if score >= limits.high_min:
        return "high"
    if score >= limits.check_min:
        return "check"
    return "low"


def apply_discrepancy_cap(flag: str, cap: str = DISCREPANCY_CONFIDENCE_CAP) -> str:
    """Lower a recorded band to the cap.

    A blank flag stays blank. A band already at or below the cap stays as it
    is, so a low score is not raised to check.
    """

    if flag not in _BAND_RANK or cap not in _BAND_RANK:
        return flag or ""
    if _BAND_RANK[flag] > _BAND_RANK[cap]:
        return cap
    return flag


def combine_confidence(flags: list[str]) -> str:
    """Worst band across a line. Any missing flag leaves the line blank."""

    if not flags:
        return ""
    if any(flag not in {"high", "check", "low"} for flag in flags):
        return ""
    if "low" in flags:
        return "low"
    if "check" in flags:
        return "check"
    return "high"
