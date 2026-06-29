#!/usr/bin/env python3
"""Build the Canada demo: coastlines for context + clustered infrastructure
points, tiled on EPSG:3978 (NAD83 / Canada Atlas Lambert).

Land comes from ``examples/data/canada_land.geojson`` (Natural Earth 50m, public
domain, clipped to Canada). ~700 synthetic infrastructure sites are sampled
*inside* the land (biased toward the populated south), each tagged with a
``category`` — airport / military / power / radio — so the most zoomed-in symbols
can show the nature of the data, while denser areas cluster into glowing blobs.

Run:  python examples/make_canada.py
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from tippykayak import Aggregation, Grid, TileOptions, build

HERE = Path(__file__).resolve().parent
LAND = HERE / "data" / "canada_land.geojson"
GEOJSON = HERE / "canada.geojson"

N_SITES = 720
# Category mix (roughly), and the southern-bias of where each tends to sit.
CATEGORIES = [
    ("airport", 0.42),
    ("power", 0.26),
    ("radio", 0.20),
    ("military", 0.12),
]


def load_land() -> list[dict]:
    return json.loads(LAND.read_text())["features"]


def sample_sites(land_features: list[dict]) -> list[dict]:
    land = unary_union([shape(f["geometry"]) for f in land_features])
    on_land = prep(land)
    minx, miny, maxx, maxy = land.bounds
    cats, weights = zip(*CATEGORIES)

    rng = random.Random(20240628)
    feats: list[dict] = []
    attempts = 0
    while len(feats) < N_SITES and attempts < N_SITES * 400:
        attempts += 1
        lon = rng.uniform(minx, maxx)
        lat = rng.uniform(max(miny, 42.0), min(maxy, 78.0))
        # Bias toward the populated south: accept lower latitudes more often.
        if rng.random() > math.exp(-(lat - 43.0) / 13.0):
            continue
        if not on_land.contains(Point(lon, lat)):
            continue
        category = rng.choices(cats, weights=weights, k=1)[0]
        feats.append(
            {
                "type": "Feature",
                "properties": {"kind": "site", "category": category},
                "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
            }
        )
    return feats


def graticule() -> list[dict]:
    # Kept inside the landmass's footprint so it reads as context without
    # sprawling past the coast (which would inflate the framing extent).
    feats = []
    for lat in (55, 65, 75):
        feats.append(
            {
                "type": "Feature",
                "properties": {"kind": "parallel"},
                "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lon in range(-132, -57, 1)]},
            }
        )
    for lon in range(-130, -59, 20):
        feats.append(
            {
                "type": "Feature",
                "properties": {"kind": "meridian"},
                "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat in range(48, 81)]},
            }
        )
    return feats


def main() -> None:
    land = load_land()
    sites = sample_sites(land)
    fc = {"type": "FeatureCollection", "features": [*land, *graticule(), *sites]}
    GEOJSON.write_text(json.dumps(fc))
    mix = {c: sum(1 for s in sites if s["properties"]["category"] == c) for c, _ in CATEGORIES}
    print(f"Wrote {GEOJSON} ({len(land)} land polys, {len(sites)} sites: {mix})")

    grid = Grid.named("EPSG3978")
    result = build(
        GEOJSON,
        HERE / "canada-3978.pmtiles",
        grid,
        TileOptions(
            layer="canada",
            min_zoom=0,
            max_zoom=8,
            simplify_pixels=1.0,
            aggregation=Aggregation(enabled=True, distance_pixels=40),
        ),
        name="Canadian infrastructure (EPSG:3978)",
    )
    print(f"  EPSG3978: {result.tile_count} tiles, z{result.min_zoom}-{result.max_zoom} -> {result.output.name}")


if __name__ == "__main__":
    main()
