#!/usr/bin/env python3
"""Generate a points-heavy Arctic dataset and build clustered PMTiles for it on
both Arctic grids: EPSG:3413 (NSIDC polar stereographic) and EPSG:3573 (North
Pole LAEA).

The data is ~600 synthetic "settlements" scattered around the circumpolar
landmasses, each with a population. Clustering aggregates them into counted,
population-summed representatives that split apart as you zoom in — Tippecanoe's
*aggregate* behaviour, on a non-WebMercator grid.

Run:  python examples/make_arctic.py
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from tippykayak import Accumulation, Aggregation, Grid, TileOptions, build

HERE = Path(__file__).resolve().parent
GEOJSON = HERE / "arctic.geojson"

# Rough circumpolar clusters: (centre lon, centre lat, spread°, how many).
REGIONS = [
    ("West Greenland", -50, 67, 6, 70),
    ("Nunavut", -95, 68, 10, 80),
    ("Alaska North Slope", -150, 69, 8, 60),
    ("Chukotka", 175, 67, 9, 50),
    ("Taymyr / Siberia", 95, 72, 12, 70),
    ("Scandinavia / Kola", 25, 69, 8, 90),
    ("Svalbard", 16, 78, 3, 30),
    ("Iceland", -19, 65, 3, 50),
    ("Yamal", 70, 68, 6, 50),
]


def settlements() -> list[dict]:
    rng = random.Random(20240627)
    feats = []
    for name, lon, lat, spread, count in REGIONS:
        for _ in range(count):
            jlon = lon + rng.uniform(-spread, spread)
            jlat = max(50.0, min(83.0, lat + rng.uniform(-spread / 2, spread / 2)))
            pop = int(10 ** rng.uniform(1.5, 4.8))  # ~30 to ~60k, skewed
            feats.append(
                {
                    "type": "Feature",
                    "properties": {"kind": "settlement", "region": name, "population": pop},
                    "geometry": {"type": "Point", "coordinates": [((jlon + 180) % 360) - 180, jlat]},
                }
            )
    return feats


def graticule() -> list[dict]:
    feats = []
    for lat in (60, 70, 80):
        feats.append(
            {
                "type": "Feature",
                "properties": {"kind": "parallel", "lat": lat},
                "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon in range(-180, 181, 2)]},
            }
        )
    return feats


def main() -> None:
    fc = {"type": "FeatureCollection", "features": [*graticule(), *settlements()]}
    GEOJSON.write_text(json.dumps(fc))
    n_pts = sum(1 for f in fc["features"] if f["geometry"]["type"] == "Point")
    print(f"Wrote {GEOJSON} ({n_pts} settlements + graticule)")

    aggregation = Aggregation(
        enabled=True,
        distance_pixels=36,
        accumulate=(
            Accumulation.parse("sum:population"),
            Accumulation.parse("max:population"),
        ),
    )
    for tms, out_name in (("EPSG3413", "arctic-3413"), ("EPSG3573", "arctic-3573")):
        grid = Grid.named(tms)
        result = build(
            GEOJSON,
            HERE / f"{out_name}.pmtiles",
            grid,
            TileOptions(layer="arctic", min_zoom=0, max_zoom=7, aggregation=aggregation),
            name=f"Arctic settlements ({tms})",
        )
        print(f"  {tms}: {result.tile_count} tiles, z{result.min_zoom}-{result.max_zoom} -> {result.output.name}")


if __name__ == "__main__":
    main()
