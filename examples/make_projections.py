#!/usr/bin/env python3
"""Tile **one** dataset onto **four** tiling schemes.

tippykayak's whole thesis in a single demo: the same source — real land ≥ 40°N,
country boundaries, and a lat/lon graticule — rendered natively on four different
TileMatrixSets, none of them Web Mercator. The viewer's projection switcher flips
between the resulting archives so you can watch the identical geography re-tile
from a pole-centred disc to a flat plate-carrée strip.

Source data (all **Natural Earth**, public domain; committed so the demo builds
offline, regenerated from the Natural Earth CDN if missing):
    * ``data/arctic_land.geojson``       1:50m land, clipped to latitude ≥ 40°
    * ``data/arctic_boundaries.geojson`` 1:50m admin-0 country boundary lines ≥ 40°

Reference lines (a lat/lon graticule, the Arctic Circle at 66.56°N, and the red
antimeridian) are generated here. Every feature is **densified** before tiling so
that straight lon/lat edges — above all the 40° clip — reproject to smooth curves
(the "chop" follows the 40th parallel, curved, in the polar views).

Tiling schemes (same data, one archive each)
    * ``EPSG:3413``   NSIDC Sea Ice Polar Stereographic North  → land-3413.pmtiles
    * ``EPSG:3573``   North Pole LAEA (Canada / Beringia)      → land-3573.pmtiles
    * ``EPSG:3978``   NAD83 / Canada Atlas Lambert (conic)     → land-3978.pmtiles
    * ``CRS84Square`` Geographic plate carrée (degrees)        → land-4326.pmtiles

(EPSG:3978 uses a square custom grid rather than morecantile's CanadianNAD83_LCC,
which is a 5×5 quad at zoom 0 and so not addressable by PMTiles' square tile IDs —
same Canada Atlas LCC projection, PMTiles-compatible tiling.)

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
BOUNDS = DATA / "arctic_boundaries.geojson"

# Upstream Natural Earth (public domain), used only to regenerate a source if absent.
NE_LAND_URL = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_land.zip"
NE_BOUNDS_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_boundary_lines_land.zip"

MIN_LAT = 40.0
SIMPLIFY_DEG = 0.01
MIN_AREA_DEG2 = 0.0002
# Max segment length (degrees) features are densified to before reprojection, so
# long straight lon/lat edges — the 40° clip especially — curve in projection.
DENSIFY_DEG = 1.0

# Reference graticule (lon/lat). The antimeridian (±180°) is drawn separately, red.
MERIDIANS = [-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150]
PARALLELS = [50, 60, 70, 80]
ARCTIC_CIRCLE_LAT = 66.5634


def _download_ne_shp(url: str, tmp: str):
    import shapefile  # pyshp — install with: pip install -e '.[examples]'

    zpath = Path(tmp) / "ne.zip"
    urllib.request.urlretrieve(url, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tmp)
    return shapefile.Reader(str(next(Path(tmp).glob("*.shp"))))


def ensure_land() -> Path:
    if LAND.exists():
        return LAND
    print(f"Regenerating {LAND.name} from Natural Earth …")
    clip = box(-180.0, MIN_LAT, 180.0, 90.0)
    feats = []
    with tempfile.TemporaryDirectory() as tmp:
        for sr in _download_ne_shp(NE_LAND_URL, tmp).iterShapes():
            if sr.bbox[3] < MIN_LAT:
                continue
            g = shape(sr.__geo_interface__)
            if not g.is_valid:
                g = g.buffer(0)
            g = g.intersection(clip).simplify(SIMPLIFY_DEG)
            for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                if not poly.is_empty and poly.area >= MIN_AREA_DEG2:
                    feats.append(_feature(poly, "land"))
    LAND.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return LAND


def ensure_bounds() -> Path:
    if BOUNDS.exists():
        return BOUNDS
    print(f"Regenerating {BOUNDS.name} from Natural Earth …")
    clip = box(-180.0, MIN_LAT, 180.0, 90.0)
    feats = []
    with tempfile.TemporaryDirectory() as tmp:
        for sr in _download_ne_shp(NE_BOUNDS_URL, tmp).iterShapes():
            if sr.bbox[3] < MIN_LAT:
                continue
            g = shape(sr.__geo_interface__).intersection(clip).simplify(SIMPLIFY_DEG)
            for ln in (g.geoms if g.geom_type.startswith("Multi") else [g]):
                if not ln.is_empty and ln.length > 0:
                    feats.append(_feature(ln, "boundary"))
    BOUNDS.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return BOUNDS


def _feature(geom, kind: str) -> dict:
    return {"type": "Feature", "properties": {"kind": kind}, "geometry": mapping(geom)}


def _frange(a: float, b: float, step: float) -> list[float]:
    out, x = [], a
    while x <= b + 1e-9:
        out.append(round(x, 4))
        x += step
    return out


def _line(coords: list, kind: str) -> dict:
    return {"type": "Feature", "properties": {"kind": kind},
            "geometry": {"type": "LineString", "coordinates": coords}}


def graticule_features() -> list[dict]:
    """Meridians, parallels, the Arctic Circle, and the red antimeridian — sampled
    finely so they curve correctly when reprojected into each grid's CRS."""
    step = DENSIFY_DEG
    feats = [_line([[lon, lat] for lat in _frange(MIN_LAT, 90.0, step)], "meridian")
             for lon in MERIDIANS]
    feats += [_line([[lon, lat] for lon in _frange(-180.0, 180.0, step)], "parallel")
              for lat in PARALLELS]
    feats.append(_line([[lon, ARCTIC_CIRCLE_LAT] for lon in _frange(-180.0, 180.0, step)],
                       "arctic_circle"))
    # ±180° coincide in the polar views and mark both edges of the geographic view.
    for lon in (-180.0, 180.0):
        feats.append(_line([[lon, lat] for lat in _frange(MIN_LAT, 90.0, step)], "antimeridian"))
    return feats


def combined_source() -> Path:
    """One FeatureCollection: densified land + boundaries + generated graticule."""
    feats = []
    for src in (ensure_land(), ensure_bounds()):
        for f in json.loads(src.read_text())["features"]:
            geom = shape(f["geometry"]).segmentize(DENSIFY_DEG)
            feats.append({"type": "Feature", "properties": f["properties"],
                          "geometry": mapping(geom)})
    feats += graticule_features()
    tmp = Path(tempfile.mkstemp(suffix=".geojson")[1])
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return tmp


# (grid id, output stem) — same source data on every scheme.
SCHEMES = [
    ("EPSG3413", "land-3413"),
    ("EPSG3573", "land-3573"),
    ("EPSG3978", "land-3978"),
    ("CRS84Square", "land-4326"),
]
MAX_ZOOM = 6


def main() -> None:
    src = combined_source()
    n = len(json.loads(src.read_text())["features"])
    print(f"Source: {n} features (land + boundaries + graticule), Natural Earth ≥{MIN_LAT:.0f}°N")
    try:
        for tms, stem in SCHEMES:
            grid = Grid.named(tms)
            result = build(
                src,
                HERE / f"{stem}.pmtiles",
                grid,
                TileOptions(layer="land", min_zoom=0, max_zoom=MAX_ZOOM,
                            simplify_pixels=1.0, min_feature_pixels=1.0),
                name=f"Land ≥{MIN_LAT:.0f}°N — {grid.tms.title} ({tms})",
            )
            print(f"  {tms:<12} {result.tile_count:>5} tiles, {result.feature_count} feats "
                  f"-> {result.output.name}")
    finally:
        src.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
