"""Point clustering / attribute aggregation — the *aggregate* step.

Instead of merely dropping points as you zoom out (which throws information
away), clustering merges nearby points into a single representative point that
carries a ``point_count`` and any accumulated attributes (sum / mean / min / max
of a numeric field). Zoom in and the cells shrink until, at the deepest zoom,
almost every cluster is a single original point again.

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
    # If set, place the cluster representative at the centre of mass weighted by
    # this numeric attribute (e.g. population) instead of the plain centroid.
    # (Cells are inherently zoom-nested: the cell size halves exactly each zoom
    # and is aligned to the CRS origin, so a parent cell splits cleanly into four.)
    weight_property: str | None = None


def _xy(feat: Feature) -> tuple[float, float]:
    """A representative coordinate, skipping GEOS for plain points."""
    geom = feat.geometry
    if geom.geom_type == "Point":
        return geom.x, geom.y
    p = geom.representative_point()
    return p.x, p.y


def cluster_points(points: list[Feature], cell_size: float, agg: Aggregation) -> list[Feature]:
    """Cluster ``points`` on a square grid of ``cell_size`` (CRS units)."""
    buckets: dict[tuple[int, int], list[Feature]] = defaultdict(list)
    for feat in points:
        x, y = _xy(feat)
        key = (math.floor(x / cell_size), math.floor(y / cell_size))
        buckets[key].append(feat)
    # Deterministic output order, independent of input ordering.
    return [_merge(buckets[k], agg) for k in sorted(buckets)]


def _weight(feat: Feature, prop: str | None) -> float:
    if prop is None:
        return 1.0
    v = feat.properties.get(prop)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return 1.0
    return float(v)


def _merge(members: list[Feature], agg: Aggregation) -> Feature:
    pts = [_xy(m) for m in members]
    weights = [_weight(m, agg.weight_property) for m in members]
    total = sum(weights) or float(len(members))
    cx = sum(x * w for (x, _), w in zip(pts, weights)) / total
    cy = sum(y * w for (_, y), w in zip(pts, weights)) / total
    centroid = Point(cx, cy)

    # Representative attributes come from the member nearest the centre of mass,
    # so the kept name/label is stable and central rather than order-dependent.
    rep = min(
        zip(members, pts),
        key=lambda mp: (mp[1][0] - cx) ** 2 + (mp[1][1] - cy) ** 2,
    )[0]
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
