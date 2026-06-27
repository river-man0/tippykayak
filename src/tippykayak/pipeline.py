"""End-to-end pipeline: GeoJSON in, non-WebMercator PMTiles out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS, Transformer

from .archive import write_pmtiles
from .encode import encode_tile
from .features import Feature, load_geojson
from .tiler import TileOptions, build_tiles
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
) -> BuildResult:
    features = load_geojson(input_path, grid, input_crs=input_crs)
    if not features:
        raise ValueError(f"No usable features found in {input_path}")

    pyramid = build_tiles(grid, features, options)

    encoded: dict[tuple[int, int, int], bytes] = {}
    for (z, col, row), layers in pyramid.items():
        encoded[(z, col, row)] = encode_tile(layers, col, row, grid.zoom(z), options.extent)

    write_pmtiles(
        output_path,
        encoded,
        grid,
        min_zoom=options.min_zoom,
        max_zoom=options.max_zoom,
        vector_layers=_vector_layers(features, options),
        geographic_bounds=_geographic_bounds(features, grid),
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


def _vector_layers(features: list[Feature], options: TileOptions) -> list[dict]:
    fields: dict[str, str] = {}
    for feat in features:
        for key, value in feat.properties.items():
            fields.setdefault(key, _json_field_type(value))
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


def _geographic_bounds(features: list[Feature], grid: Grid) -> tuple[float, float, float, float]:
    """Inverse-project the data's CRS-space extent back to lon/lat for the header."""
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for f in features:
        fx0, fy0, fx1, fy1 = f.geometry.bounds
        minx, miny = min(minx, fx0), min(miny, fy0)
        maxx, maxy = max(maxx, fx1), max(maxy, fy1)

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
