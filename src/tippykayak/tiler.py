"""The core tiler: turn projected features into a tile pyramid.

This is tippykayak's answer to the three things Tippecanoe does so well —
*simplify*, *drop*, *aggregate* — except every decision is made in the projected
space of an arbitrary TileMatrixSet rather than Web Mercator.

It implements:

* **simplify** — Douglas-Peucker, with a tolerance scaled to each zoom's ground
  resolution, so detail is shed smoothly as you zoom out.
* **drop (size)** — features smaller than a few pixels at a given zoom are not
  emitted there; they "switch on" at the first zoom where they're big enough.
* **drop (density)** — point features are thinned with a deterministic, zoom-
  stable dot-dropping gate (a point shown at zoom z is always shown at z+1).
* **aggregate** — alternatively, points are clustered (see :mod:`.aggregate`),
  merging nearby points into representatives that carry a count and accumulated
  attributes instead of being thrown away.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .aggregate import Aggregation, cluster_points
from .features import Feature
from .tms import Grid


@dataclass
class TileOptions:
    layer: str = "tippykayak"
    min_zoom: int = 0
    max_zoom: int = 6
    extent: int = 4096
    # Simplification tolerance, expressed in *pixels* at each zoom. Converted to
    # CRS units per zoom via that zoom's resolution.
    simplify_pixels: float = 1.0
    # A feature is emitted at a zoom only once its longer projected side spans at
    # least this many pixels. Set to 0 to disable size-based dropping.
    min_feature_pixels: float = 1.5
    # Per-zoom point retention factor for dot-dropping. 1.0 keeps every point at
    # every zoom; 0.5 keeps ~half as many points each zoom level coarser.
    point_retain_per_zoom: float = 1.0
    # Tile edge buffer, in pixels, so features straddling tile borders render
    # without seams.
    buffer_pixels: float = 8.0
    # Point clustering / attribute aggregation. When enabled it replaces the
    # dot-dropping below for point features.
    aggregation: Aggregation = field(default_factory=Aggregation)


# A tile keyed by (z, x, y); value maps layer name -> list of (geometry, props).
TilePyramid = dict[tuple[int, int, int], dict[str, list[tuple[BaseGeometry, dict]]]]

_POINT_TYPES = ("Point", "MultiPoint")


def build_tiles(grid: Grid, features: Iterable[Feature], options: TileOptions) -> TilePyramid:
    feats = list(features)
    points = [f for f in feats if f.geometry.geom_type in _POINT_TYPES]
    shapes = [f for f in feats if f.geometry.geom_type not in _POINT_TYPES]
    pyramid: TilePyramid = defaultdict(lambda: defaultdict(list))
    agg = options.aggregation

    for z in range(options.min_zoom, options.max_zoom + 1):
        zg = grid.zoom(z)
        tol = options.simplify_pixels * zg.resolution
        size_threshold = options.min_feature_pixels * zg.resolution
        buffer_crs = options.buffer_pixels * zg.resolution

        # Lines and polygons: size-based dropping + per-zoom simplification.
        for feat in shapes:
            if not _shape_visible_at(feat, z, options, size_threshold):
                continue
            geom = feat.geometry
            if tol > 0:
                simplified = geom.simplify(tol, preserve_topology=True)
                if not simplified.is_empty:
                    geom = simplified
            for (col, row), clipped in _split_into_tiles(geom, zg, buffer_crs):
                pyramid[(z, col, row)][options.layer].append((clipped, feat.properties))

        # Points: cluster-aggregate, or fall back to zoom-stable dot-dropping.
        zoom_points = [
            p for p in points
            if p.forced_min_zoom is None or z >= p.forced_min_zoom
        ]
        if agg.enabled:
            cell = agg.distance_pixels * zg.resolution
            rendered = cluster_points(zoom_points, cell, agg)
        else:
            rendered = [p for p in zoom_points if _point_survives(p, z, options)]
        for feat in rendered:
            for (col, row), clipped in _split_into_tiles(feat.geometry, zg, buffer_crs):
                pyramid[(z, col, row)][options.layer].append((clipped, feat.properties))

    # Drop the defaultdict machinery for a plain dict result.
    return {k: {ln: v for ln, v in layers.items()} for k, layers in pyramid.items()}


def _shape_visible_at(feat: Feature, z: int, options: TileOptions, size_threshold: float) -> bool:
    if feat.forced_min_zoom is not None and z < feat.forced_min_zoom:
        return False
    # Size-based dropping for lines/polygons.
    if options.min_feature_pixels > 0 and z < options.max_zoom:
        if feat.extent < size_threshold:
            return False
    return True


def _point_survives(feat: Feature, z: int, options: TileOptions) -> bool:
    """Deterministic, zoom-stable dot-dropping.

    Each point gets a stable value in [0, 1). At zoom ``z`` we keep points whose
    value is below ``retain ** (max_zoom - z)``. Because the threshold only grows
    as z increases, a point visible at z is guaranteed visible at every z+1.
    """
    retain = options.point_retain_per_zoom
    if retain >= 1.0:
        return True
    threshold = retain ** (options.max_zoom - z)
    return _stable_unit(feat) < threshold


def _stable_unit(feat: Feature) -> float:
    rep = feat.geometry.representative_point()
    digest = hashlib.sha1(f"{rep.x:.3f},{rep.y:.3f}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _split_into_tiles(geom: BaseGeometry, zg, buffer_crs: float):
    """Yield ((col, row), clipped_geometry) for every tile the geometry touches."""
    minx, miny, maxx, maxy = geom.bounds
    # Rows increase downward, so the top edge (maxy) is the minimum row and the
    # bottom edge (miny) is the maximum row.
    col_lo, row_lo = zg.tile_for(minx, maxy)  # upper-left tile
    col_hi, row_hi = zg.tile_for(maxx, miny)  # lower-right tile
    col_lo, row_lo = zg.clamp(col_lo, row_lo)
    col_hi, row_hi = zg.clamp(col_hi, row_hi)

    for col in range(col_lo, col_hi + 1):
        for row in range(row_lo, row_hi + 1):
            tminx, tminy, tmaxx, tmaxy = zg.tile_bounds(col, row)
            clip_box = box(
                tminx - buffer_crs,
                tminy - buffer_crs,
                tmaxx + buffer_crs,
                tmaxy + buffer_crs,
            )
            clipped = geom.intersection(clip_box)
            if clipped.is_empty:
                continue
            yield (col, row), clipped
