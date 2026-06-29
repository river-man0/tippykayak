#!/usr/bin/env python3
"""Build the Greenland demo from **real** OpenStreetMap data.

Unlike the other examples (which sample synthetic points for context), this one
tiles a genuine `Geofabrik <https://download.geofabrik.de/>`_ extract — the whole
point of tippykayak's ``.osm.pbf`` support — onto **EPSG:3413** (NSIDC Sea Ice
Polar Stereographic North, centred on lon −45° so Greenland sits upright and
centred). Real coastlines, the ice sheet (``natural=glacier``), lakes, waterways
and settlements, all tiled natively in a polar CRS.

The ~26 MB extract is downloaded on first run (not committed); the resulting
``.pmtiles`` archive is the committed demo artifact.

Run:  python examples/make_greenland.py
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

from tippykayak import Grid, TileOptions, build

HERE = Path(__file__).resolve().parent
PBF = HERE / "data" / "greenland-latest.osm.pbf"
PBF_URL = "https://download.geofabrik.de/north-america/greenland-latest.osm.pbf"
OUT = HERE / "greenland-3413.pmtiles"


def ensure_extract() -> Path:
    if PBF.exists():
        return PBF
    PBF.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {PBF_URL} …")
    # Geofabrik 302-redirects to the dated file; urllib follows it by default.
    urllib.request.urlretrieve(PBF_URL, PBF)
    print(f"  saved {PBF} ({PBF.stat().st_size / 1e6:.0f} MB)")
    return PBF


def main() -> None:
    pbf = ensure_extract()
    grid = Grid.named("EPSG3413")
    result = build(
        pbf,
        OUT,
        grid,
        TileOptions(
            layer="greenland",
            min_zoom=0,
            max_zoom=9,
            simplify_pixels=1.0,
            # Real coastlines are dense; let small far-zoom features drop out.
            min_feature_pixels=1.5,
        ),
        name="Greenland — OpenStreetMap (EPSG:3413)",
    )
    print(
        f"  EPSG3413: {result.tile_count} tiles, {result.feature_count} features, "
        f"z{result.min_zoom}-{result.max_zoom} -> {result.output.name}"
    )


if __name__ == "__main__":
    main()
