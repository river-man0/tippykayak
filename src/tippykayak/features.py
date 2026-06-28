"""Loading source features and projecting them into the grid CRS.

Input is either ordinary **GeoJSON** in geographic coordinates (EPSG:4326 by
default) or a **Geofabrik / OpenStreetMap ``.osm.pbf``** extract (see
:mod:`tippykayak.osm`). Whichever the source, before any tiling happens we
reproject every geometry into the target grid's CRS so that all downstream maths
(tiling, simplification, clipping) happens in the *projected* space of the chosen
TileMatrixSet — never Web Mercator.

Both readers funnel through :func:`_project_raw`, which takes raw
``(geometry, properties, forced_min_zoom)`` tuples in the input CRS and returns
projected :class:`Feature`s, so the input format is decoupled from the tiler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from .tms import Grid

# A raw, pre-reprojection feature: (geometry in the input CRS, properties,
# optional explicit floor zoom). Shared by the GeoJSON and OSM readers.
RawFeature = tuple[BaseGeometry, dict, Optional[int]]


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


def load_features(
    path: str | Path,
    grid: Grid,
    *,
    input_crs: str | int = 4326,
    theme=None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[Feature]:
    """Load any supported input, projected into ``grid``'s CRS.

    The input format is chosen by extension: ``.pbf`` / ``.osm.pbf`` is read as
    an OpenStreetMap extract (always EPSG:4326, with ``theme`` / ``bbox``),
    anything else is read as GeoJSON (honouring ``input_crs``).
    """
    suffixes = [s.lower() for s in Path(path).suffixes]
    if suffixes and suffixes[-1] == ".pbf":
        # Imported lazily so GeoJSON-only use doesn't require pyosmium.
        from .osm import DEFAULT_THEME, iter_osm_raw

        raw = iter_osm_raw(path, theme=theme or DEFAULT_THEME, bbox=bbox)
        return list(_project_raw(raw, grid, 4326))
    return load_geojson(path, grid, input_crs=input_crs)


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
    def raw() -> Iterator[RawFeature]:
        for feat in _raw_features(geojson):
            geom = feat.get("geometry")
            if not geom:
                continue
            props = feat.get("properties") or {}
            yield shape(geom), props, props.get(min_zoom_property)

    yield from _project_raw(raw(), grid, input_crs)


def _project_raw(
    raw: Iterable[RawFeature], grid: Grid, input_crs: str | int
) -> Iterator[Feature]:
    """Reproject raw input-CRS features into ``grid``'s CRS, repairing validity.

    The single place both the GeoJSON and OSM readers converge: it keeps the
    input format independent of the tiler, which only ever sees projected
    :class:`Feature`s.
    """
    project = _reprojector(CRS.from_user_input(input_crs), grid)
    for geom, props, forced_min_zoom in raw:
        projected = project(geom)
        # Reprojection routinely produces self-intersections; repair once here so
        # the per-tile clipping downstream never trips over invalid geometry.
        if not projected.is_valid:
            projected = make_valid(projected)
        if projected.is_empty:
            continue
        yield Feature(
            geometry=projected,
            properties=props,
            forced_min_zoom=forced_min_zoom,
        )


def _raw_features(geojson: dict) -> Iterable[dict]:
    kind = geojson.get("type")
    if kind == "FeatureCollection":
        return geojson.get("features", [])
    if kind == "Feature":
        return [geojson]
    # A bare geometry.
    return [{"type": "Feature", "geometry": geojson, "properties": {}}]
