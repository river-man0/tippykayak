#!/usr/bin/env python3
"""Build the Arctic demo: real coastlines for context + clustered settlements,
tiled on both Arctic grids (EPSG:3413 and EPSG:3573).

Land comes from ``examples/data/arctic_land.geojson`` (Natural Earth 50m, public
domain, clipped to lat >= 40). Settlements are sampled *inside* the land polygons
so the clusters sit on land rather than floating in the ocean, each with a
log-normal population used to weight the cluster centroids and to accumulate.

Run:  python examples/make_arctic.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from tippykayak import Accumulation, Aggregation, Grid, TileOptions, build

HERE = Path(__file__).resolve().parent
LAND = HERE / "data" / "arctic_land.geojson"
GEOJSON = HERE / "arctic.geojson"

N_SETTLEMENTS = 700
LAT_RANGE = (50.0, 83.0)


def load_land() -> list[dict]:
    return json.loads(LAND.read_text())["features"]


def sample_settlements(land_features: list[dict]) -> list[dict]:
    land = unary_union([shape(f["geometry"]) for f in land_features])
    on_land = prep(land)
    minx, miny, maxx, maxy = land.bounds
    miny, maxy = max(miny, LAT_RANGE[0]), min(maxy, LAT_RANGE[1])

    rng = random.Random(20240627)
    feats: list[dict] = []
    attempts = 0
    while len(feats) < N_SETTLEMENTS and attempts < N_SETTLEMENTS * 200:
        attempts += 1
        lon = rng.uniform(minx, maxx)
        lat = rng.uniform(miny, maxy)
        if not on_land.contains(Point(lon, lat)):
            continue
        pop = int(10 ** rng.uniform(1.5, 5.2))  # ~30 to ~160k, heavily skewed
        feats.append(
            {
                "type": "Feature",
                "properties": {"kind": "settlement", "population": pop},
                "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
            }
        )
    return feats


def graticule() -> list[dict]:
    return [
        {
            "type": "Feature",
            "properties": {"kind": "parallel", "lat": lat},
            "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon in range(-180, 181, 2)]},
        }
        for lat in (50, 60, 70, 80)
    ]


def main() -> None:
    land = load_land()
    settlements = sample_settlements(land)
    fc = {"type": "FeatureCollection", "features": [*land, *graticule(), *settlements]}
    GEOJSON.write_text(json.dumps(fc))
    print(f"Wrote {GEOJSON} ({len(land)} land polys, {len(settlements)} settlements)")

    aggregation = Aggregation(
        enabled=True,
        distance_pixels=44,
        weight_property="population",
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
            TileOptions(
                layer="arctic",
                min_zoom=0,
                max_zoom=7,
                simplify_pixels=1.0,
                aggregation=aggregation,
            ),
            name=f"Arctic settlements ({tms})",
        )
        print(f"  {tms}: {result.tile_count} tiles, z{result.min_zoom}-{result.max_zoom} -> {result.output.name}")


if __name__ == "__main__":
    main()
