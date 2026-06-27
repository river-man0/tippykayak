#!/usr/bin/env python3
"""Generate a synthetic Antarctic dataset and build polar PMTiles from it.

The data is deliberately self-contained (no network/downloads): a polar
graticule, a wavy "coastline" ring, and a scatter of research stations. Rendered
on a Web Mercator map these would be uselessly distorted near the pole — which is
exactly why tippykayak tiles them on the UPS Antarctic grid instead.

Run:  python examples/make_sample.py
Then: python -m http.server  (and open viewer/index.html)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from tippykayak import Grid, TileOptions, build

HERE = Path(__file__).resolve().parent
GEOJSON = HERE / "antarctica.geojson"
PMTILES = HERE / "antarctica.pmtiles"


def graticule() -> list[dict]:
    features = []
    # Latitude circles.
    for lat in range(-85, -55, 5):
        ring = [[lon, lat] for lon in range(-180, 181, 2)]
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "parallel", "lat": lat},
                "geometry": {"type": "LineString", "coordinates": ring},
            }
        )
    # Meridians.
    for lon in range(-180, 180, 30):
        line = [[lon, lat] for lat in range(-89, -54)]
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "meridian", "lon": lon},
                "geometry": {"type": "LineString", "coordinates": line},
            }
        )
    return features


def coastline() -> dict:
    # A wavy ring near 70S, as a filled polygon.
    pts = []
    for deg in range(0, 361, 2):
        rad = math.radians(deg)
        lat = -70 + 3 * math.sin(6 * rad)
        pts.append([deg - 180, lat])
    pts.append(pts[0])
    return {
        "type": "Feature",
        "properties": {"kind": "iceshelf", "name": "Synthetic Ice Shelf"},
        "geometry": {"type": "Polygon", "coordinates": [pts]},
    }


def stations() -> list[dict]:
    spec = [
        ("Amundsen-Scott", 0.0, -89.99),
        ("Vostok", 106.8, -78.46),
        ("Concordia", 123.35, -75.1),
        ("McMurdo", 166.67, -77.85),
        ("Rothera", -68.13, -67.57),
        ("Mawson", 62.87, -67.6),
    ]
    return [
        {
            "type": "Feature",
            "properties": {"kind": "station", "name": name},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }
        for name, lon, lat in spec
    ]


def main() -> None:
    fc = {"type": "FeatureCollection", "features": [*graticule(), coastline(), *stations()]}
    GEOJSON.write_text(json.dumps(fc))
    print(f"Wrote {GEOJSON} ({len(fc['features'])} features)")

    grid = Grid.named("UPSAntarcticWGS84Quad")
    result = build(
        GEOJSON,
        PMTILES,
        grid,
        TileOptions(layer="antarctica", min_zoom=0, max_zoom=6, simplify_pixels=1.0),
        name="Synthetic Antarctica",
    )
    print(
        f"Wrote {result.output} — {result.tile_count} tiles on {result.grid}, "
        f"z{result.min_zoom}-{result.max_zoom}"
    )


if __name__ == "__main__":
    main()
