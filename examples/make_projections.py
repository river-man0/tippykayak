#!/usr/bin/env python3
"""Tile **one** dataset onto **four** tiling schemes.

tippykayak's whole thesis in a single demo: the same source — real countries
≥ 40°N and a lat/lon graticule — rendered natively on four different
TileMatrixSets, none of them Web Mercator. The viewer's projection switcher flips
between the resulting archives so you can watch the identical geography re-tile
from a pole-centred disc to a flat plate-carrée strip.

Source data (**Natural Earth**, public domain; committed so the demo builds
offline, regenerated from the Natural Earth CDN if missing):
    * ``data/arctic_countries.geojson`` 1:50m admin-0 countries, clipped ≥ 40°N,
      each polygon tagged with its country ``name`` (the viewer fills each country
      a subdued colour).

Reference lines (a lat/lon graticule, the Arctic Circle at 66.56°N, and the red
antimeridian) are generated here. Every feature is **densified** before tiling so
straight lon/lat edges — above all the 40° clip — reproject to smooth curves (the
"chop" follows the 40th parallel, curved, in the polar/conic views). On the
EPSG:3978 conic the red antimeridian brackets the ~36° undefined wedge.

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
COUNTRIES = DATA / "arctic_countries.geojson"

# Upstream Natural Earth (public domain), used only to regenerate the source if absent.
NE_COUNTRIES_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"

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


def ensure_countries() -> Path:
    """The committed country clip; regenerate from Natural Earth if missing."""
    if COUNTRIES.exists():
        return COUNTRIES
    import shapefile  # pyshp — install with: pip install -e '.[examples]'

    print(f"Regenerating {COUNTRIES.name} from Natural Earth …")
    clip = box(-180.0, MIN_LAT, 180.0, 90.0)
    feats = []
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "ne.zip"
        urllib.request.urlretrieve(NE_COUNTRIES_URL, zpath)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        reader = shapefile.Reader(str(next(Path(tmp).glob("*.shp"))))
        name_i = [f[0] for f in reader.fields[1:]].index("NAME")
        for sr in reader.iterShapeRecords():
            if sr.shape.bbox[3] < MIN_LAT:
                continue
            g = shape(sr.shape.__geo_interface__)
            if not g.is_valid:
                g = g.buffer(0)
            g = g.intersection(clip).simplify(SIMPLIFY_DEG)
            for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
                if not poly.is_empty and poly.area >= MIN_AREA_DEG2:
                    feats.append({"type": "Feature", "geometry": mapping(poly),
                                  "properties": {"kind": "country", "name": sr.record[name_i]}})
    COUNTRIES.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return COUNTRIES


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
    # ±180° coincide in the polar views, mark both edges of the geographic view, and
    # on the EPSG:3978 conic bracket the undefined wedge.
    for lon in (-180.0, 180.0):
        feats.append(_line([[lon, lat] for lat in _frange(MIN_LAT, 90.0, step)], "antimeridian"))
    return feats


def combined_features() -> list[dict]:
    """Densified countries + generated graticule, as a feature list."""
    feats = []
    for f in json.loads(ensure_countries().read_text())["features"]:
        geom = shape(f["geometry"]).segmentize(DENSIFY_DEG)
        feats.append({"type": "Feature", "properties": f["properties"],
                      "geometry": mapping(geom)})
    return feats + graticule_features()


def split_at_meridian(feats: list[dict], cut_lon: float) -> list[dict]:
    """Split every feature at a meridian so nothing spans it.

    A Lambert conformal conic tears the globe along its central meridian's
    antipode (lon₀ + 180). Geometry crossing that line reprojects into a smear
    across the projection's ~36° *undefined wedge*, healing over the very
    limitation we want to show. Splitting the source there leaves the wedge as a
    clean empty gap between the two cut edges.
    """
    west = box(-180.0, MIN_LAT, cut_lon, 90.0)
    east = box(cut_lon, MIN_LAT, 180.0, 90.0)
    out = []
    for f in feats:
        geom = shape(f["geometry"])
        for half in (west, east):
            piece = geom.intersection(half)
            if not piece.is_empty:
                out.append({"type": "Feature", "properties": f["properties"],
                            "geometry": mapping(piece)})
    return out


def wedge_feature(cut_lon: float, eps: float = 0.01) -> dict:
    """A thin lon sliver straddling the branch cut.

    Its two long edges sit just west/east of the cut, so in the conic they
    reproject to the two edges of the undefined wedge (~36° apart) while the sliver
    itself fills the gap between them — a shaded "no projection here" pie-slice. In
    any projection without a cut there (polar/geographic) the 0.02°-wide sliver
    stays invisibly thin, so it needs no per-projection special-casing.
    """
    lats = _frange(MIN_LAT, 90.0, DENSIFY_DEG)
    west = [[cut_lon - eps, lat] for lat in reversed(lats)]  # pole → 40° (west edge)
    east = [[cut_lon + eps, lat] for lat in lats]            # 40° → pole (east edge)
    ring = west + east + [west[0]]
    return {"type": "Feature", "properties": {"kind": "wedge"},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _write_temp(feats: list[dict]) -> Path:
    tmp = Path(tempfile.mkstemp(suffix=".geojson")[1])
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return tmp


# (grid id, output stem, cut meridian or None). Same source everywhere; the conic
# is split at its branch cut (lon₀ −95 + 180 = 85°E) so its undefined wedge shows.
SCHEMES = [
    ("EPSG3413", "land-3413", None),
    ("EPSG3573", "land-3573", None),
    ("EPSG3978", "land-3978", 85.0),
    ("CRS84Square", "land-4326", None),
]
MAX_ZOOM = 6


def main() -> None:
    base = combined_features()
    print(f"Source: {len(base)} features (countries + graticule), Natural Earth ≥{MIN_LAT:.0f}°N")
    for tms, stem, cut in SCHEMES:
        feats = split_at_meridian(base, cut) + [wedge_feature(cut)] if cut is not None else base
        src = _write_temp(feats)
        try:
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
