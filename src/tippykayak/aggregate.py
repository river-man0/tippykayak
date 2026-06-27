"""Point clustering / attribute aggregation.

This is tippykayak's take on Tippecanoe's *aggregate* step. Instead of merely
dropping points as you zoom out (which throws information away), clustering merges
nearby points into a single representative point that carries a ``point_count``
and any accumulated attributes (sum / mean / min / max of a numeric field). Zoom
in and the cells shrink until, at the deepest zoom, almost every cluster is a
single original point again.

Clustering happens globally in the grid CRS at each zoom (not per tile), so a
cluster never splits across a tile boundary.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import fmean

from shapely.geometry import Point

from .features import Feature


@dataclass(frozen=True)
class Accumulation:
    """Accumulate ``source`` over a cluster with ``op``, writing to ``out``."""

    op: str  # one of: sum, mean, min, max, count
    source: str
    out: str

    @classmethod
    def parse(cls, spec: str) -> "Accumulation":
        """Parse a CLI spec ``op:source[:out]`` (e.g. ``sum:population``)."""
        parts = spec.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"Invalid accumulate spec {spec!r}; expected op:source[:out]")
        op, source = parts[0], parts[1]
        if op not in _OPS:
            raise ValueError(f"Unknown accumulate op {op!r}; choose from {sorted(_OPS)}")
        out = parts[2] if len(parts) == 3 else f"{source}_{op}"
        return cls(op=op, source=source, out=out)


_OPS = {
    "sum": sum,
    "mean": fmean,
    "min": min,
    "max": max,
    "count": len,
}


@dataclass
class Aggregation:
    enabled: bool = False
    # Cluster cell size in pixels; converted to CRS units per zoom.
    distance_pixels: float = 32.0
    count_property: str = "point_count"
    accumulate: tuple[Accumulation, ...] = field(default_factory=tuple)


def cluster_points(points: list[Feature], cell_size: float, agg: Aggregation) -> list[Feature]:
    """Cluster ``points`` on a square grid of ``cell_size`` (CRS units)."""
    buckets: dict[tuple[int, int], list[Feature]] = defaultdict(list)
    for feat in points:
        p = feat.geometry.representative_point()
        key = (math.floor(p.x / cell_size), math.floor(p.y / cell_size))
        buckets[key].append(feat)
    # Deterministic output order, independent of input ordering.
    return [_merge(buckets[k], agg) for k in sorted(buckets)]


def _merge(members: list[Feature], agg: Aggregation) -> Feature:
    pts = [m.geometry.representative_point() for m in members]
    centroid = Point(fmean(p.x for p in pts), fmean(p.y for p in pts))

    # Representative attributes come from the member nearest the centroid, so the
    # kept name/label is stable and central rather than order-dependent.
    rep = min(
        members,
        key=lambda m: (m.geometry.representative_point().x - centroid.x) ** 2
        + (m.geometry.representative_point().y - centroid.y) ** 2,
    )
    props = dict(rep.properties)
    props[agg.count_property] = len(members)

    for acc in agg.accumulate:
        if acc.op == "count":
            props[acc.out] = len(members)
            continue
        values = [
            m.properties[acc.source]
            for m in members
            if isinstance(m.properties.get(acc.source), (int, float))
            and not isinstance(m.properties.get(acc.source), bool)
        ]
        if values:
            props[acc.out] = _OPS[acc.op](values)

    geometry = rep.geometry if len(members) == 1 else centroid
    return Feature(geometry=geometry, properties=props)
