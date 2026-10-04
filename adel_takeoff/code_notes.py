"""Florida code edition from the permit-issue date, and drawing RFIs.

A code claim cites a section or is marked unknown. Drawing text that names
NEC 2023 is an RFI. It does not set the job's code edition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from adel_takeoff.constants import PERMIT_CODE_CUTOFF

SECTION_RE = re.compile(r"\b(?:NEC|FBC)\s+\d+(?:\.\d+)*\b", re.IGNORECASE)
NEC_2023_RE = re.compile(r"\bNEC\s*2023\b", re.IGNORECASE)
REQUIREMENT_RE = re.compile(
    r"\b(?:shall|must|required|comply|complies|compliance)\b", re.IGNORECASE
)
CODE_WORD_RE = re.compile(r"\bcode\b", re.IGNORECASE)
POWER_RE = re.compile(r"\bpower\b|line[\s-]?voltage|branch circuit", re.IGNORECASE)
CLASS2_RE = re.compile(
    r"class\s*2|communications|communication", re.IGNORECASE
)
RACEWAY_RE = re.compile(r"raceway|conduit", re.IGNORECASE)
SEPARATE_RE = re.compile(
    r"separate\s+(raceway|conduit)|each\s+in\s+(?:its|their)\s+own",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Edition:
    fbc: str
    nec: str
    basis: str


@dataclass(frozen=True)
class Rfi:
    topic: str
    detail: str
    sections: str
    disposition: str = "rfi"


@dataclass(frozen=True)
class CodeClaim:
    statement: str
    section: str
    rendered: str


def format_code_statement(statement: str, section: str | None) -> str:
    """Print a code statement only with a section citation or the word unknown."""

    cited = (section or "").strip() or "unknown"
    return f"{statement} (section: {cited})"


def edition_for(permit_issue_date: date | None) -> Edition:
    """Select the Florida cycle from the permit-issue date.

    On or before 2026-12-31: FBC 8th Edition and NEC 2020.
    After 2026-12-31: FBC 9th Edition and NEC 2023.
    A missing date stays unknown. Drawing text is not a basis.
    """

    if permit_issue_date is None:
        return Edition(fbc="unknown", nec="unknown", basis="unknown")
    if permit_issue_date > PERMIT_CODE_CUTOFF:
        return Edition(
            fbc="FBC 9th Edition",
            nec="NEC 2023",
            basis="permit-issue date",
        )
    return Edition(
        fbc="FBC 8th Edition",
        nec="NEC 2020",
        basis="permit-issue date",
    )


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[\n.]+", text)
    return [part.strip() for part in parts if part.strip()]


def analyze_drawing_text(text: str) -> tuple[list[Rfi], list[CodeClaim]]:
    """Read code-related notes from drawing text. Do not treat them as fact."""

    rfis: list[Rfi] = []
    claims: list[CodeClaim] = []

    if NEC_2023_RE.search(text or ""):
        rfis.append(
            Rfi(
                topic="Drawing text says NEC 2023",
                detail=(
                    "The drawing text includes NEC 2023. This is an RFI. "
                    "It is not recorded as the applicable code edition."
                ),
                sections="",
                disposition="rfi",
            )
        )

    mixed = False
    for sentence in _sentences(text or ""):
        has_power = POWER_RE.search(sentence) is not None
        has_class2 = CLASS2_RE.search(sentence) is not None
        has_raceway = RACEWAY_RE.search(sentence) is not None
        separated = SEPARATE_RE.search(sentence) is not None
        if has_power and has_class2 and has_raceway and not separated:
            mixed = True
            break
    if mixed:
        statement = (
            "Drawings are unclear whether power is mixed with Class 2 or "
            "communications in one raceway. This condition is restricted "
            "under the cited sections. Confirm the raceway arrangement"
        )
        sections = "NEC 725.136 and NEC 800.133"
        rfis.append(
            Rfi(
                topic="Power and Class 2 or communications raceway",
                detail=format_code_statement(statement, sections),
                sections=sections,
                disposition="rfi",
            )
        )

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if NEC_2023_RE.search(stripped) and not REQUIREMENT_RE.search(stripped):
            continue
        has_requirement = REQUIREMENT_RE.search(stripped) is not None
        has_code_word = CODE_WORD_RE.search(stripped) is not None
        found = SECTION_RE.findall(stripped)
        if not has_requirement:
            continue
        if not has_code_word and not found:
            continue
        if found:
            cited = " and ".join(section.upper().replace("  ", " ") for section in found)
            # Normalize "NEC    300.11" style from the regex match.
            cited = re.sub(r"\s+", " ", cited)
        else:
            cited = "unknown"
        claims.append(
            CodeClaim(
                statement=stripped,
                section=cited,
                rendered=format_code_statement(stripped, cited),
            )
        )

    return rfis, claims
