"""The routing seam is importable and produces no route. The count path does not call it."""

import ast
from pathlib import Path

import pytest

from adel_takeoff.routing.protocol import (
    ConduitRouter,
    RouteRequest,
    UnimplementedConduitRouter,
    get_router,
)
from adel_takeoff.takeoff import run_takeoff
from tests.drawings import build_sample_pdf


def test_router_is_unimplemented_and_produces_no_route():
    router = get_router()
    assert isinstance(router, UnimplementedConduitRouter)
    assert isinstance(router, ConduitRouter)
    with pytest.raises(NotImplementedError, match="No route is produced"):
        router.route(RouteRequest(job_id="job-1"))


def test_count_path_does_not_import_routing():
    root = Path(__file__).resolve().parents[1] / "adel_takeoff"
    for path in root.rglob("*.py"):
        if "routing" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all("routing" not in name for name in names), path
    result = run_takeoff(build_sample_pdf())
    assert not hasattr(result, "routes")
    assert result.items
