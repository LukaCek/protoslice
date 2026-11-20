"""Deterministic fixture checks; no external packages or OrcaSlicer needed."""

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def parse_ascii_stl_triangles(path: Path):
    vertices = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        fields = raw_line.strip().split()
        if fields[:1] == ["vertex"]:
            assert len(fields) == 4
            vertices.append(tuple(float(v) for v in fields[1:]))
    assert len(vertices) % 3 == 0
    return [vertices[i:i + 3] for i in range(0, len(vertices), 3)]


def signed_mesh_volume_mm3(triangles):
    def cross(a, b):
        return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
    return sum(sum(a[i]*cross(b,c)[i] for i in range(3)) / 6.0 for a,b,c in triangles)


def test_cube_fixture_is_deterministic_and_watertight_baseline():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    entry = manifest["fixtures"][0]
    path = FIXTURES / entry["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    triangles = parse_ascii_stl_triangles(path)
    assert len(triangles) == entry["triangle_count"] == 12
    points = [v for triangle in triangles for v in triangle]
    assert [min(p[i] for p in points) for i in range(3)] == entry["expected_bounds_mm"]["min"]
    assert [max(p[i] for p in points) for i in range(3)] == entry["expected_bounds_mm"]["max"]
    assert entry["expected_mesh_volume_mm3"] == 8000.0
    assert math.isclose(signed_mesh_volume_mm3(triangles), entry["expected_mesh_volume_mm3"], rel_tol=0, abs_tol=1e-9)
    # Every undirected edge must occur twice, with opposite directions.
    directed_edges = Counter()
    for a, b, c in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            directed_edges[(u, v)] += 1
    undirected = Counter()
    for (u, v), count in directed_edges.items():
        undirected[frozenset((u, v))] += count
    assert len(undirected) == 18
    assert all(count == 2 for count in undirected.values())
    assert all(directed_edges[(v, u)] == directed_edges[(u, v)] for u, v in directed_edges)
