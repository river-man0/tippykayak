#!/usr/bin/env python3
"""Build the Arctic demo from **real** OpenStreetMap data, tiled on both
north-pole grids (EPSG:3413 and EPSG:3573).

The two polar grids are centred on the North Pole, so the demo draws the thing
those projections exist to show — the **circumpolar Arctic** — entirely from
genuine OSM-derived data, no synthetic points:

* **Land / coastlines** from the OpenStreetMap *land polygons* product
  (`osmdata.openstreetmap.de <https://osmdata.openstreetmap.de/>`_, the
  simplified EPSG:3857 build assembled from OSM ``natural=coastline`` ways — the
  same coastline data the standard slippy map is rendered from). Reprojected to
  lon/lat, clipped to the Arctic (lat ≥ 40), lightly simplified and de-specked.
* The **Greenland ice sheet** (``natural=glacier``) from a `Geofabrik
  <https://download.geofabrik.de/>`_ ``.osm.pbf`` — by far the dominant Arctic
  land-ice mass. OSM exposes the other Arctic ice caps (Canadian Archipelago,
  Svalbard, Severnaya Zemlya …) only through very large regional extracts, so
  this demo ships Greenland's ice; add more extracts to ``GLACIER_PBFS`` below to
  extend it. (Floating **sea** ice is not in OpenStreetMap at all — it is
  seasonal satellite data — so it is out of scope here.)

Both downloads (~24 MB land + ~26 MB Greenland) are fetched on first run and are
git-ignored; the resulting ``.pmtiles`` archives are the committed demo
artifacts. The Greenland extract is shared with ``make_greenland.py``.

Run:  python examples/make_arctic.py
"""

from __future__ import annotations

import json
import urllib.request
import zipfile
from pathlib import Path

import shapefile  # pyshp — install with: pip install -e '.[examples]'
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shapely_transform
from pyproj import Transformer

from tippykayak import Grid, Rule, TileOptions, build, iter_osm_raw

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
GEOJSON = HERE / "arctic.geojson"

# OSM land polygons (simplified, EPSG:3857), assembled from OSM coastlines.
LAND_URL = "https://osmdata.openstreetmap.de/download/simplified-land-polygons-complete-3857.zip"
LAND_ZIP = DATA / "simplified-land-polygons-3857.zip"
LAND_SHP = DATA / "simplified-land-polygons-complete-3857" / "simplified_land_polygons.shp"

# Geofabrik extracts to pull `natural=glacier` from (shared with make_greenland).
GLACIER_PBFS = {
    DATA / "greenland-latest.osm.pbf":
        "https://download.geofabrik.de/north-america/greenland-latest.osm.pbf",
}

# Arctic framing and generalisation. The grids run to ~40°N; simplify to ~0.4 km
# (sub-pixel by the max zoom on these ~12,000 km-wide grids) and drop specks so a
# whole-hemisphere overview stays light.
ARCTIC = box(-180.0, 40.0, 180.0, 90.0)
SIMPLIFY_DEG = 0.004
MIN_AREA_DEG2 = 0.0008
MAX_ZOOM = 7

_to_4326 = Transformer.from_crs(3857, 4326, always_xy=True)


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url} …")
    urllib.request.urlretrieve(url, dest)  # Geofabrik/osmdata redirects are followed
    print(f"  saved {dest.name} ({dest.stat().st_size / 1e6:.0f} MB)")


def ensure_land() -> Path:
    if LAND_SHP.exists():
        return LAND_SHP
    if not LAND_ZIP.exists():
        _download(LAND_URL, LAND_ZIP)
    print(f"Unzipping {LAND_ZIP.name} …")
    with zipfile.ZipFile(LAND_ZIP) as z:
        z.extractall(DATA)
    return LAND_SHP


def ensure_glacier_pbfs() -> list[Path]:
    paths = []
    for dest, url in GLACIER_PBFS.items():
        if not dest.exists():
            _download(url, dest)
        paths.append(dest)
    return paths


def land_features() -> list[dict]:
    """Real OSM coastlines: reproject 3857 → lon/lat, clip to the Arctic,
    simplify, and drop islands too small to read at hemisphere scale."""
    shp = ensure_land()
    feats: list[dict] = []
    for sr in shapefile.Reader(str(shp)).iterShapes():
        if sr.bbox[3] < 4.0e6:  # 3857 y of the polygon top is below ~33°N — skip
            continue
        geom = shape(sr.__geo_interface__)
        if not geom.is_valid:
            geom = geom.buffer(0)
        geom = shapely_transform(lambda xs, ys: _to_4326.transform(xs, ys), geom)
        if geom.bounds[3] < 40.0:
            continue
        geom = geom.intersection(ARCTIC).simplify(SIMPLIFY_DEG)
        if geom.is_empty:
            continue
        parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in parts:
            if not poly.is_empty and poly.area >= MIN_AREA_DEG2:
                feats.append({"type": "Feature", "properties": {"class": "land"},
                              "geometry": mapping(poly)})
    return feats


def glacier_features() -> list[dict]:
    """Real OSM land ice (`natural=glacier`) from the Geofabrik extract(s)."""
    theme = (Rule("glacier", "area", "natural", frozenset({"glacier"})),)
    feats: list[dict] = []
    for pbf in ensure_glacier_pbfs():
        for geom, props, _ in iter_osm_raw(pbf, theme):
            if geom.area < MIN_AREA_DEG2 and geom.geom_type != "Point":
                continue
            feats.append({"type": "Feature", "properties": dict(props),
                          "geometry": mapping(geom.simplify(SIMPLIFY_DEG))})
    return feats


def main() -> None:
    land = land_features()
    glaciers = glacier_features()
    fc = {"type": "FeatureCollection", "features": [*land, *glaciers]}
    GEOJSON.write_text(json.dumps(fc))
    print(f"Wrote {GEOJSON.name} ({len(land)} land polys, {len(glaciers)} glacier polys)")

    for tms, out_name in (("EPSG3413", "arctic-3413"), ("EPSG3573", "arctic-3573")):
        result = build(
            GEOJSON,
            HERE / f"{out_name}.pmtiles",
            Grid.named(tms),
            TileOptions(
                layer="arctic",
                min_zoom=0,
                max_zoom=MAX_ZOOM,
                simplify_pixels=1.0,
                min_feature_pixels=1.0,
            ),
            name=f"Arctic — OpenStreetMap ({tms})",
        )
        print(
            f"  {tms}: {result.tile_count} tiles, {result.feature_count} features, "
            f"z{result.min_zoom}-{result.max_zoom} -> {result.output.name}"
        )


if __name__ == "__main__":
    main()
