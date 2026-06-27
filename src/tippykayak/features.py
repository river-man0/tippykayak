"""Loading source features and projecting them into the grid CRS.

Input is ordinary GeoJSON in geographic coordinates (EPSG:4326 by default).
Before any tiling happens we reproject every geometry into the target grid's
CRS so that all downstream maths (tiling, simplification, clipping) happens in
the *projected* space of the chosen TileMatrixSet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from .tms import Grid


@dataclass
class Feature:
    """A single source feature, geometry already in the grid CRS."""

    geometry: BaseGeometry
    properties: dict
    # Optional explicit floor zoom from the GeoJSON (``tippykayak:minzoom``);
    # otherwise tippykayak computes one from the feature's projected size.
    forced_min_zoom: int | None = None
    extent: float = field(init=False)

    def __post_init__(self) -> None:
        minx, miny, maxx, maxy = self.geometry.bounds
        # "Extent" is the longer side of the projected bounding box, in CRS
        # units. It drives size-based dropping: a feature that is smaller than a
        # handful of pixels at a given zoom is not worth emitting there.
        self.extent = max(maxx - minx, maxy - miny)


def _reprojector(input_crs: CRS, grid: Grid):
    transformer = Transformer.from_crs(input_crs, grid.crs, always_xy=True)

    def fn(geom: BaseGeometry) -> BaseGeometry:
        return shapely_transform(
            lambda xs, ys, z=None: transformer.transform(xs, ys), geom
        )

    return fn


def load_geojson(
    path: str | Path,
    grid: Grid,
    input_crs: str | int = 4326,
    min_zoom_property: str = "tippykayak:minzoom",
) -> list[Feature]:
    """Read a GeoJSON file and return features projected into ``grid``'s CRS."""
    data = json.loads(Path(path).read_text())
    return list(
        iter_features(data, grid, input_crs=input_crs, min_zoom_property=min_zoom_property)
    )


def iter_features(
    geojson: dict,
    grid: Grid,
    input_crs: str | int = 4326,
    min_zoom_property: str = "tippykayak:minzoom",
) -> Iterator[Feature]:
    raw = _raw_features(geojson)
    project = _reprojector(CRS.from_user_input(input_crs), grid)
    for feat in raw:
        geom = feat.get("geometry")
        if not geom:
            continue
        projected = project(shape(geom))
        if projected.is_empty:
            continue
        props = feat.get("properties") or {}
        yield Feature(
            geometry=projected,
            properties=props,
            forced_min_zoom=props.get(min_zoom_property),
        )


def _raw_features(geojson: dict) -> Iterable[dict]:
    kind = geojson.get("type")
    if kind == "FeatureCollection":
        return geojson.get("features", [])
    if kind == "Feature":
        return [geojson]
    # A bare geometry.
    return [{"type": "Feature", "geometry": geojson, "properties": {}}]
