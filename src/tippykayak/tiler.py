"""The core tiler: turn projected features into a tile pyramid.

It does the three things a good vector-tile generator must — *simplify*, *drop*,
*aggregate* — except every decision is made in the projected space of an
arbitrary TileMatrixSet rather than Web Mercator.

The workflow borrows the structural ideas that make tippecanoe elegant (see
``docs/tippecanoe.md``), re-derived for any square-quad TMS:

* **quadtree descent** — lines and polygons are clipped into the tile(s) of the
  starting zoom once, then each tile's geometry is *split into its four
  children* recursively. No geometry is ever re-examined at full length: the
  work at each tile is proportional to the detail actually inside it, instead
  of every zoom re-clipping every full feature. This works on any grid whose
  matrices double per zoom — exactly the square-quad property PMTiles already
  requires — so it holds for morecantile defaults and custom grids alike.
* **simplify** — Douglas-Peucker per tile, with a tolerance scaled to each
  zoom's ground resolution (in the CRS's own units), so detail is shed smoothly
  as you zoom out. Clipping first keeps segment endpoints pinned at the tile
  buffer edge, so neighbouring tiles stay seam-free.
* **drop (size) + polygon dust** — features smaller than a few pixels at a
  given zoom are not emitted there; they "switch on" at the first zoom where
  they're big enough. Dropped *polygons* are not silently discarded: their area
  accumulates, and each time it crosses a pixel's worth a placeholder square of
  that size is emitted (tippecanoe's tiny-polygon trick), so a field of
  sub-pixel buildings still reads as a settlement at low zoom.
* **drop (density)** — point features are thinned with a deterministic, zoom-
  stable dot-dropping gate (a point shown at zoom z is always shown at z+1).
* **aggregate** — alternatively, points are clustered (see :mod:`.aggregate`),
  merging nearby points into representatives that carry a count and accumulated
  attributes instead of being thrown away.

:func:`guess_max_zoom` implements tippecanoe's ``-zg``: pick the maxzoom whose
MVT quantization step matches the spacing of the data, measured in the grid's
own CRS units so it is correct on polar, conic and geographic grids alike.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from .aggregate import Aggregation, cluster_points
from .features import Feature
from .tms import Grid, ZoomGrid


@dataclass
class TileOptions:
    layer: str = "tippykayak"
    min_zoom: int = 0
    # ``None`` asks the pipeline to guess a maxzoom from the data's spacing
    # (tippecanoe's ``-zg``) via :func:`guess_max_zoom`.
    max_zoom: int | None = 6
    extent: int = 4096
    # Simplification tolerance, expressed in *pixels* at each zoom. Converted to
    # CRS units per zoom via that zoom's resolution.
    simplify_pixels: float = 1.0
    # A feature is emitted at a zoom only once its longer projected side spans at
    # least this many pixels. Set to 0 to disable size-based dropping.
    min_feature_pixels: float = 1.5
    # When size-dropping polygons, accumulate their area and emit a placeholder
    # square each time a pixel's worth accumulates, so dense fields of sub-pixel
    # polygons keep their visual mass at low zoom instead of vanishing.
    polygon_dust: bool = True
    # Per-zoom point retention factor for dot-dropping. 1.0 keeps every point at
    # every zoom; 0.5 keeps ~half as many points each zoom level coarser.
    point_retain_per_zoom: float = 1.0
    # Tile edge buffer, in pixels, so features straddling tile borders render
    # without seams.
    buffer_pixels: float = 8.0
    # Hard per-tile budgets (tippecanoe's 500 KB / 200 000-feature guardrails).
    # When a tile exceeds them, the least important features (smallest first)
    # are shed until it fits. 0 disables either limit. Bytes are measured on
    # the gzipped MVT — what a PMTiles range request actually transfers.
    max_tile_features: int = 200_000
    max_tile_bytes: int = 512_000
    # Point clustering / attribute aggregation. When enabled it replaces the
    # dot-dropping below for point features.
    aggregation: Aggregation = field(default_factory=Aggregation)


# A tile keyed by (z, x, y); value maps layer name -> list of (geometry, props).
TilePyramid = dict[tuple[int, int, int], dict[str, list[tuple[BaseGeometry, dict]]]]

_POINT_TYPES = ("Point", "MultiPoint")
_AREA_TYPES = ("Polygon", "MultiPolygon", "GeometryCollection")


def build_tiles(grid: Grid, features: Iterable[Feature], options: TileOptions) -> TilePyramid:
    if options.max_zoom is None:
        raise ValueError("max_zoom must be resolved (see guess_max_zoom) before tiling")
    feats = list(features)
    points = [f for f in feats if f.geometry.geom_type in _POINT_TYPES]
    shapes = [f for f in feats if f.geometry.geom_type not in _POINT_TYPES]
    pyramid: TilePyramid = defaultdict(lambda: defaultdict(list))

    _tile_shapes(grid, shapes, options, pyramid)
    _tile_points(grid, points, options, pyramid)

    # Drop the defaultdict machinery for a plain dict result.
    return {k: {ln: v for ln, v in layers.items()} for k, layers in pyramid.items()}


# ---------------------------------------------------------------------------
# Lines & polygons: quadtree descent.
# ---------------------------------------------------------------------------

# A shape being carried down the tree: (geometry clipped to this tile's buffered
# window — still at full detail — and the source feature for properties,
# projected extent and forced minzoom).
_Carried = tuple[BaseGeometry, Feature]


def _tile_shapes(grid: Grid, shapes: list[Feature], options: TileOptions, pyramid: TilePyramid) -> None:
    if not shapes:
        return
    zg = grid.zoom(options.min_zoom)
    buffer_crs = options.buffer_pixels * zg.resolution
    roots: dict[tuple[int, int], list[_Carried]] = defaultdict(list)
    for feat in shapes:
        for (col, row), clipped in _split_into_tiles(feat.geometry, zg, buffer_crs):
            roots[(col, row)].append((clipped, feat))
    for (col, row) in sorted(roots):
        _descend(grid, options.min_zoom, col, row, roots[(col, row)], options, pyramid)


def _descend(
    grid: Grid,
    z: int,
    col: int,
    row: int,
    carried: list[_Carried],
    options: TileOptions,
    pyramid: TilePyramid,
) -> None:
    """Emit one tile, then split its geometry into the four child tiles.

    ``carried`` holds full-detail geometry already clipped to this tile's
    buffered window. Because the buffer is fixed in *pixels*, its CRS size
    halves each zoom, so every child's buffered window nests inside this one —
    clipping the child from the parent's clip is exact, never lossy.
    """
    zg = grid.zoom(z)
    emitted = _emit_shapes(carried, z, zg, options)
    if emitted:
        pyramid[(z, col, row)][options.layer].extend(emitted)

    if z >= options.max_zoom:
        return
    czg = grid.zoom(z + 1)
    buffer_crs = options.buffer_pixels * czg.resolution
    for dc in (0, 1):
        for dr in (0, 1):
            ccol, crow = 2 * col + dc, 2 * row + dr
            if ccol >= czg.matrix_width or crow >= czg.matrix_height:
                continue
            minx, miny, maxx, maxy = czg.tile_bounds(ccol, crow)
            wminx, wminy = minx - buffer_crs, miny - buffer_crs
            wmaxx, wmaxy = maxx + buffer_crs, maxy + buffer_crs
            window = box(wminx, wminy, wmaxx, wmaxy)
            child: list[_Carried] = []
            for geom, feat in carried:
                gminx, gminy, gmaxx, gmaxy = geom.bounds
                if gminx > wmaxx or gmaxx < wminx or gminy > wmaxy or gmaxy < wminy:
                    continue
                clipped = _safe_intersection(geom, window)
                if not clipped.is_empty:
                    child.append((clipped, feat))
            if child:
                _descend(grid, z + 1, ccol, crow, child, options, pyramid)


def _emit_shapes(
    carried: list[_Carried], z: int, zg: ZoomGrid, options: TileOptions
) -> list[tuple[BaseGeometry, dict]]:
    """This tile's renderable features at zoom ``z``: visibility, dust, simplify."""
    tol = options.simplify_pixels * zg.resolution
    size_threshold = options.min_feature_pixels * zg.resolution
    dropping = options.min_feature_pixels > 0 and z < options.max_zoom
    # A "pixel's worth" of polygon: the same threshold that gates visibility,
    # squared. Dropped polygon area accumulates against it (in tile order, so
    # the result is deterministic) and surfaces as placeholder squares.
    dust_quantum = size_threshold * size_threshold
    dust_area = 0.0

    out: list[tuple[BaseGeometry, dict]] = []
    for geom, feat in carried:
        if feat.forced_min_zoom is not None and z < feat.forced_min_zoom:
            continue
        if dropping and feat.extent < size_threshold:
            if options.polygon_dust and geom.geom_type in _AREA_TYPES:
                dust_area += geom.area
                if dust_area >= dust_quantum:
                    dust_area -= dust_quantum
                    p = geom.representative_point()
                    half = size_threshold / 2.0
                    square = box(p.x - half, p.y - half, p.x + half, p.y + half)
                    out.append((square, feat.properties))
            continue
        if tol > 0:
            simplified = geom.simplify(tol, preserve_topology=True)
            if not simplified.is_empty:
                geom = simplified
        out.append((geom, feat.properties))
    return out


def _safe_intersection(geom: BaseGeometry, window: BaseGeometry) -> BaseGeometry:
    try:
        return geom.intersection(window)
    except GEOSException:
        # Last-ditch repair if clipping or simplification reintroduced invalidity.
        return make_valid(geom).intersection(window)


# ---------------------------------------------------------------------------
# Points: per-zoom clustering or zoom-stable dot-dropping.
# ---------------------------------------------------------------------------


def _tile_points(grid: Grid, points: list[Feature], options: TileOptions, pyramid: TilePyramid) -> None:
    if not points:
        return
    agg = options.aggregation
    for z in range(options.min_zoom, options.max_zoom + 1):
        zg = grid.zoom(z)
        buffer_crs = options.buffer_pixels * zg.resolution
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


def _split_into_tiles(geom: BaseGeometry, zg: ZoomGrid, buffer_crs: float):
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
            clipped = _safe_intersection(geom, clip_box)
            if clipped.is_empty:
                continue
            yield (col, row), clipped


# ---------------------------------------------------------------------------
# Guessing a maxzoom from the data (tippecanoe's -zg).
# ---------------------------------------------------------------------------


def guess_max_zoom(grid: Grid, features: Iterable[Feature], options: TileOptions) -> int:
    """Choose the shallowest maxzoom that can still resolve the data.

    This is tippecanoe's ``-zg``, restated in the grid's own CRS units so it is
    correct on metre-based and degree-based grids alike. At maxzoom, one step
    of the MVT integer grid (``tile_span / extent``) should be no coarser than
    a target spacing derived from the data, using tippecanoe's own recipe:

    * for **points**, spacing is the distance between consecutive points along
      a Z-order (Morton) sort — a one-sort stand-in for nearest-neighbour
      distance. The target is the *nearby* end of that distribution
      (``exp(mean − 1.5·stddev)`` of the log spacings), halved: closely
      spaced features are the ones that must be told apart.
    * for **lines/polygons**, spacing is segment length, and the target is an
      **eighth** of its geometric mean, so quantization sits well below the
      scale of the drawn detail rather than exactly at it.

    A cumulative tile-count guardrail (~2M tiles, estimated from the summed
    feature envelope areas) then caps runaway guesses, exactly as tippecanoe
    caps its own.
    """
    if options.max_zoom is not None:
        return options.max_zoom

    feats = list(features)
    targets: list[float] = []
    seg_stats = _log_stats(_segment_lengths(feats))
    if seg_stats:
        mean, _ = seg_stats
        targets.append(math.exp(mean) / 8.0)
    pt_stats = _log_stats(_point_spacings(feats, grid))
    if pt_stats:
        mean, stddev = pt_stats
        targets.append(math.exp(mean - 1.5 * stddev) / 2.0)

    floor = max(options.min_zoom, grid.min_zoom)
    top = grid.max_zoom
    if not targets:
        return max(options.min_zoom, min(top, 6))
    want = min(targets)

    guess = top
    for z in range(floor, top + 1):
        if grid.zoom(z).tile_span / options.extent <= want:
            guess = z
            break

    # Tile-count guardrail: don't guess a pyramid deeper than ~2M tiles.
    area_sum = 0.0
    for f in feats:
        minx, miny, maxx, maxy = f.geometry.bounds
        area_sum += (maxx - minx) * (maxy - miny)
    total_tiles = 0.0
    for z in range(floor + 1, guess + 1):
        span = grid.zoom(z).tile_span
        total_tiles += math.ceil(area_sum / (span * span))
        if total_tiles > 2 * 1024 * 1024:
            guess = z - 1
            break

    return max(guess, options.min_zoom)


_MAX_SPACING_SAMPLES = 100_000


def _log_stats(values: Iterable[float]) -> tuple[float, float] | None:
    """Mean and standard deviation of ``log(v)`` (Welford), or None if empty."""
    count = 0
    mean = 0.0
    m2 = 0.0
    for v in values:
        if v <= 0:
            continue
        count += 1
        x = math.log(v)
        delta = x - mean
        mean += delta / count
        m2 += delta * (x - mean)
    if count == 0:
        return None
    return mean, math.sqrt(m2 / count)


def _segment_lengths(features: list[Feature]) -> Iterator[float]:
    emitted = 0
    for feat in features:
        if feat.geometry.geom_type in _POINT_TYPES:
            continue
        for coords in _coord_runs(feat.geometry):
            for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
                yield math.hypot(x1 - x0, y1 - y0)
                emitted += 1
                if emitted >= _MAX_SPACING_SAMPLES:
                    return


def _point_spacings(features: list[Feature], grid: Grid) -> list[float]:
    points: list[tuple[float, float]] = []
    for feat in features:
        geom = feat.geometry
        if geom.geom_type == "Point":
            points.append((geom.x, geom.y))
        elif geom.geom_type == "MultiPoint":
            points.extend((p.x, p.y) for p in geom.geoms)
    if len(points) < 2:
        return []

    minx, miny, maxx, maxy = grid.crs_bounds()
    span = max(maxx - minx, maxy - miny) or 1.0

    def morton(pt: tuple[float, float]) -> int:
        xi = min(65535, max(0, int((pt[0] - minx) / span * 65536)))
        yi = min(65535, max(0, int((pt[1] - miny) / span * 65536)))
        code = 0
        for bit in range(16):
            code |= ((xi >> bit) & 1) << (2 * bit) | ((yi >> bit) & 1) << (2 * bit + 1)
        return code

    points.sort(key=morton)
    return [
        math.hypot(x1 - x0, y1 - y0)
        for (x0, y0), (x1, y1) in zip(points, points[1:])
    ][:_MAX_SPACING_SAMPLES]


def _coord_runs(geom: BaseGeometry) -> Iterator[list[tuple[float, float]]]:
    """Yield each vertex run (line or ring) of a geometry as coordinate lists."""
    gt = geom.geom_type
    if gt == "LineString":
        yield list(geom.coords)
    elif gt == "Polygon":
        yield list(geom.exterior.coords)
        for ring in geom.interiors:
            yield list(ring.coords)
    elif gt in ("MultiLineString", "MultiPolygon", "GeometryCollection"):
        for part in geom.geoms:
            yield from _coord_runs(part)
