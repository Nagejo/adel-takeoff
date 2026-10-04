"""Seam for a future conduit router.

v1 does not route conduit, size wire, or compute voltage drop. The symbol
count path does not import this module and does not call ``route``.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RouteRequest:
    """Input a future router would accept. v1 never builds a route from this."""

    job_id: str


@dataclass(frozen=True)
class RouteResult:
    """A route result. The v1 router does not construct one."""

    implemented: bool
    routes: tuple


@runtime_checkable
class ConduitRouter(Protocol):
    """Extension point. A later release can register a real router here."""

    def route(self, request: RouteRequest) -> RouteResult:
        """Return conduit routes for a job. v1 has no implementation."""


class UnimplementedConduitRouter:
    """The only router shipped in v1. It produces no route."""

    def route(self, request: RouteRequest) -> RouteResult:
        raise NotImplementedError(
            "Conduit routing is not implemented. No route is produced."
        )


def get_router() -> ConduitRouter:
    """Return the v1 router. Callers outside the count path may import this."""

    return UnimplementedConduitRouter()
