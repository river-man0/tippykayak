#!/usr/bin/env python3
"""Tile **one** land dataset onto **four** tiling schemes.

This is tippykayak's whole thesis in a single demo: the same source data — real
land ≥ 40°N — rendered natively on four different TileMatrixSets, none of them
Web Mercator. The viewer's projection switcher flips between the resulting
archives so you can watch the identical coastlines re-tile from a pole-centred
disc to a flat plate-carrée strip.

Source data
    ``examples/data/arctic_land.geojson`` — **Natural Earth** 1:50m physical land
    (`ne_50m_land`, public domain), clipped to latitude ≥ 40° and lightly
    simplified. Committed so the demo builds offline; if it is missing this
    script regenerates it from the upstream Natural Earth CDN.

Tiling schemes (same data, one archive each)
    * ``EPSG:3413``   NSIDC Sea Ice Polar Stereographic North  → land-3413.pmtiles
    * ``EPSG:3573``   North Pole LAEA (Canada / Beringia)      → land-3573.pmtiles
    * ``EPSG:3978``   NAD83 / Canada Atlas Lambert (conic)     → land-3978.pmtiles
    * ``CRS84Square`` Geographic plate carrée (degrees)        → land-4326.pmtiles

Run:  python examples/make_projections.py
"""

from __future__ import annotations

import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from shapely.geometry import box, mapping, shape

from tippykayak import Grid, TileOptions, build

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LAND = DATA / "arctic_land.geojson"

# Upstream Natural Earth (public domain) used only to regenerate LAND if absent.
NE_URL = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_land.zip"
MIN_LAT = 40.0
SIMPLIFY_DEG = 0.01
MIN_AREA_DEG2 = 0.0002

# EPSG:3978 is a conic centred on lon −95°; a conic is only meaningful within
# ~90° of its central meridian, so the same land is windowed to the western
# hemisphere for that view (exactly the region a Canada atlas shows) — this keeps
# far-side land from smearing to the grid edge. Everything from the dateline east
# to ~5°W: all of North America, Greenland and Iceland.
LCC_LON0 = -95.0
LCC_WINDOW = box(-169.0, MIN_LAT, LCC_LON0 + 90.0, 90.0)


def ensure_land() -> Path:
    """The committed Natural Earth land clip; regenerate from the CDN if missing."""
    if LAND.exists():
        return LAND
    import shapefile  # pyshp — install with: pip install -e '.[examples]'

    print(f"Regenerating {LAND.name} from Natural Earth …")
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "ne.zip"
        urllib.request.urlretrieve(NE_URL, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        shp = next(Path(tmp).glob("*.shp"))
        clip = box(-180.0, MIN_LAT, 180.0, 90.0)
        feats = []
        for sr in shapefile.Reader(str(shp)).iterShapes():
            if sr.bbox[3] < MIN_LAT:
                continue
            g = shape(sr.__geo_interface__)
            if not g.is_valid:
                g = g.buffer(0)
            g = g.intersection(clip).simplify(SIMPLIFY_DEG)
            if g.is_empty:
                continue
            for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                if not poly.is_empty and poly.area >= MIN_AREA_DEG2:
                    feats.append({"type": "Feature", "properties": {"kind": "land"},
                                  "geometry": mapping(poly)})
        LAND.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return LAND


def _windowed(src: Path, window) -> Path:
    """Write a temp GeoJSON of `src` clipped to `window` (for the conic view)."""
    fc = json.loads(src.read_text())
    out = []
    for feat in fc["features"]:
        g = shape(feat["geometry"]).intersection(window)
        if g.is_empty:
            continue
        out.append({"type": "Feature", "properties": feat["properties"], "geometry": mapping(g)})
    tmp = Path(tempfile.mkstemp(suffix=".geojson")[1])
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": out}))
    return tmp


# (grid id, output stem, max zoom, lon-window or None)
SCHEMES = [
    ("EPSG3413", "land-3413", 6, None),
    ("EPSG3573", "land-3573", 6, None),
    ("EPSG3978", "land-3978", 6, LCC_WINDOW),
    ("CRS84Square", "land-4326", 6, None),
]


def main() -> None:
    land = ensure_land()
    n = len(json.loads(land.read_text())["features"])
    print(f"Source: {land.name} ({n} land polygons, Natural Earth ≥{MIN_LAT:.0f}°N)")

    for tms, stem, max_zoom, window in SCHEMES:
        src = _windowed(land, window) if window is not None else land
        grid = Grid.named(tms)
        result = build(
            src,
            HERE / f"{stem}.pmtiles",
            grid,
            TileOptions(
                layer="land",
                min_zoom=0,
                max_zoom=max_zoom,
                simplify_pixels=1.0,
                min_feature_pixels=1.0,
            ),
            name=f"Land ≥{MIN_LAT:.0f}°N — {grid.tms.title} ({tms})",
        )
        if window is not None:
            src.unlink(missing_ok=True)
        print(f"  {tms:<12} {result.tile_count:>5} tiles, {result.feature_count} feats "
              f"-> {result.output.name}")


if __name__ == "__main__":
    main()
