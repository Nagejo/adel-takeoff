"""Direct cost, markup, and labor-rate checks.

Direct cost uses only numbers the estimator entered. Blank material unit
cost and blank labor hours contribute nothing. Markup has no default.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from adel_takeoff.constants import (
    JOURNEYMAN_DEFAULT,
    JOURNEYMAN_WARNING,
    LABOR_RATES,
    MAX_MARKUP,
)


class MarkupRejected(ValueError):
    """The estimator sent a markup this job will not store."""


def parse_markup(raw: str | None) -> Decimal | None:
    """Return a markup decimal, or None when blank.

    Blank means do not apply markup. Values greater than 5 are rejected.
    There is no default.
    """

    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MarkupRejected("Markup must be a decimal such as 0.35.") from exc
    if not value.is_finite():
        raise MarkupRejected("Markup must be a decimal such as 0.35.")
    if value < 0:
        raise MarkupRejected("Markup must not be negative.")
    if value > MAX_MARKUP:
        raise MarkupRejected("Markup greater than 5 is rejected.")
    return value


def parse_rate(raw: str, label: str) -> Decimal:
    text = str(raw).strip()
    if text == "":
        raise MarkupRejected(f"{label} rate is required.")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise MarkupRejected(f"{label} rate must be a number.") from exc
    if not value.is_finite() or value <= 0:
        raise MarkupRejected(f"{label} rate must be greater than zero.")
    return value


def journeyman_default_left_in_place(rate: Decimal) -> bool:
    return Decimal(rate) == JOURNEYMAN_DEFAULT


def journeyman_warning(rate: Decimal) -> str:
    if journeyman_default_left_in_place(rate):
        return JOURNEYMAN_WARNING
    return ""


def line_direct_cost(
    quantity: Decimal | int | None,
    material_unit_cost: Decimal | None,
    labor_hours: Decimal | None,
    labor_rate: Decimal | None,
) -> Decimal | None:
    """Direct cost for one line. None when there is no quantity to price."""

    if quantity is None:
        return None
    material = material_unit_cost if material_unit_cost is not None else Decimal("0")
    hours = labor_hours if labor_hours is not None else Decimal("0")
    rate = labor_rate if labor_rate is not None else Decimal("0")
    return (material * Decimal(quantity)) + (hours * rate)


def apply_markup(direct: Decimal, markup: Decimal | None) -> Decimal | None:
    """Return the marked-up total, or None when markup is blank."""

    if markup is None:
        return None
    return direct * (Decimal("1") + markup)


def rate_table() -> tuple[tuple[str, str, Decimal], ...]:
    return LABOR_RATES
