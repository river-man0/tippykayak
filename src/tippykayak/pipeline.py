"""End-to-end pipeline: GeoJSON in, non-WebMercator PMTiles out."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from pyproj import CRS, Transformer

from .archive import write_pmtiles
from .encode import encode_tile
from .features import Feature, load_features
from .tiler import TileOptions, build_tiles, guess_max_zoom
from .tms import Grid


@dataclass
class BuildResult:
    output: Path
    tile_count: int
    feature_count: int
    min_zoom: int
    max_zoom: int
    grid: str


def build(
    input_path: str | Path,
    output_path: str | Path,
    grid: Grid,
    options: TileOptions,
    *,
    input_crs: str | int = 4326,
    name: str = "tippykayak",
    theme=None,
    bbox: tuple[float, float, float, float] | None = None,
) -> BuildResult:
    features = load_features(
        input_path, grid, input_crs=input_crs, theme=theme, bbox=bbox
    )
    if not features:
        raise ValueError(f"No usable features found in {input_path}")

    # max_zoom=None means "guess from the data" (tippecanoe's -zg): the
    # shallowest zoom whose quantization step resolves the data's spacing.
    if options.max_zoom is None:
        options = replace(options, max_zoom=guess_max_zoom(grid, features, options))
    _require_square_quad(grid, options.max_zoom)

    pyramid = build_tiles(grid, features, options)

    encoded: dict[tuple[int, int, int], bytes] = {}
    for (z, col, row), layers in pyramid.items():
        encoded[(z, col, row)] = encode_tile(
            layers,
            col,
            row,
            grid.zoom(z),
            options.extent,
            max_features=options.max_tile_features,
            max_bytes=options.max_tile_bytes,
        )

    # Clamp to the grid's own extent: a client can't frame beyond the tileable
    # area, and for a conic fed global data the far side projects to enormous
    # coordinates that would otherwise blow the framing up. No-op when the data
    # already sits inside the grid (the polar/geographic cases).
    data_extent = _clamp_extent(_projected_extent(features), grid.crs_bounds())
    write_pmtiles(
        output_path,
        encoded,
        grid,
        min_zoom=options.min_zoom,
        max_zoom=options.max_zoom,
        vector_layers=_vector_layers(features, options),
        geographic_bounds=_geographic_bounds(data_extent, grid),
        data_extent=data_extent,
        name=name,
    )

    return BuildResult(
        output=Path(output_path),
        tile_count=len(encoded),
        feature_count=len(features),
        min_zoom=options.min_zoom,
        max_zoom=options.max_zoom,
        grid=grid.id,
    )


def _require_square_quad(grid: Grid, max_zoom: int) -> None:
    """Reject grids PMTiles can't address.

    PMTiles tile IDs walk a Hilbert curve over a square ``2^z × 2^z`` grid, so a
    scheme must have a single tile at its top zoom and double on both axes. Most
    TileMatrixSets qualify, but some geographic ones (e.g. ``WorldCRS84Quad``) are
    ``2×1`` at zoom 0 and would fail deep inside the writer with an opaque "tile
    x/y outside zoom level bounds". Fail early with a fix instead.

    The same property is what lets the tiler descend the pyramid as a quadtree
    (each tile splits into exactly four children), so verify the doubling
    through every zoom that will be tiled, not just zoom 0.
    """
    z0 = grid.zoom(grid.min_zoom)
    if z0.matrix_width != 1 or z0.matrix_height != 1:
        raise ValueError(
            f"Grid '{grid.id}' is not PMTiles-addressable: its zoom-{grid.min_zoom} "
            f"matrix is {z0.matrix_width}×{z0.matrix_height}, but PMTiles requires a "
            f"square 2^z×2^z quad (one tile at zoom 0). For geographic tiling use a "
            f"square grid such as 'CRS84Square'."
        )
    for z in range(grid.min_zoom, max_zoom):
        parent, child = grid.zoom(z), grid.zoom(z + 1)
        if (
            child.matrix_width != 2 * parent.matrix_width
            or child.matrix_height != 2 * parent.matrix_height
        ):
            raise ValueError(
                f"Grid '{grid.id}' is not a quadtree between zooms {z} and {z + 1} "
                f"({parent.matrix_width}×{parent.matrix_height} → "
                f"{child.matrix_width}×{child.matrix_height}); PMTiles addressing "
                f"and tippykayak's tiler both require matrices that double per zoom."
            )


def _vector_layers(features: list[Feature], options: TileOptions) -> list[dict]:
    fields: dict[str, str] = {}
    for feat in features:
        for key, value in feat.properties.items():
            fields.setdefault(key, _json_field_type(value))

    # Fields synthesised by clustering aren't on the source features, so declare
    # them explicitly.
    agg = options.aggregation
    if agg.enabled:
        fields[agg.count_property] = "Number"
        for acc in agg.accumulate:
            fields[acc.out] = "Number"

    return [
        {
            "id": options.layer,
            "minzoom": options.min_zoom,
            "maxzoom": options.max_zoom,
            "fields": fields,
        }
    ]


def _json_field_type(value) -> str:
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, (int, float)):
        return "Number"
    return "String"


def _projected_extent(features: list[Feature]) -> tuple[float, float, float, float]:
    """The data's bounding box in the grid's projected CRS (CRS units)."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for f in features:
        fx0, fy0, fx1, fy1 = f.geometry.bounds
        minx, miny = min(minx, fx0), min(miny, fy0)
        maxx, maxy = max(maxx, fx1), max(maxy, fy1)
    return (minx, miny, maxx, maxy)


def _clamp_extent(
    extent: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Intersect a data extent with the grid's CRS bounds (fall back to the grid
    bounds if the two don't overlap, which shouldn't happen for real data)."""
    minx, miny, maxx, maxy = extent
    bminx, bminy, bmaxx, bmaxy = bounds
    cx0, cy0 = max(minx, bminx), max(miny, bminy)
    cx1, cy1 = min(maxx, bmaxx), min(maxy, bmaxy)
    if cx1 <= cx0 or cy1 <= cy0:
        return bounds
    return (cx0, cy0, cx1, cy1)


def _geographic_bounds(extent: tuple[float, float, float, float], grid: Grid) -> tuple[float, float, float, float]:
    """Inverse-project the data's CRS-space extent back to lon/lat for the header."""
    minx, miny, maxx, maxy = extent
    to_geographic = Transformer.from_crs(grid.crs, CRS.from_epsg(4326), always_xy=True)
    lons, lats = to_geographic.transform(
        [minx, maxx, minx, maxx],
        [miny, maxy, maxy, miny],
    )
    finite = [
        (lon, lat)
        for lon, lat in zip(lons, lats)
        if abs(lon) <= 180 and abs(lat) <= 90
    ]
    if not finite:
        return (-180.0, -90.0, 180.0, 90.0)
    xs = [p[0] for p in finite]
    ys = [p[1] for p in finite]
    return (min(xs), min(ys), max(xs), max(ys))
