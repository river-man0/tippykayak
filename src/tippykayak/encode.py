"""Encode a tile's projected geometry into a gzipped MVT blob.

The key detail: we hand ``mapbox_vector_tile`` the geometry in *CRS units* and a
``quantize_bounds`` equal to the tile's exact CRS bounds. The encoder then scales
into the 0..extent tile grid and flips the y axis. Geometry inside the buffer
falls slightly outside 0..extent, which is exactly what MVT clients expect.

Budgets: tippecanoe's key serving guarantee is that no tile is ever too big
(500 KB / 200 000 features by default). We enforce the same here — if a tile
busts its byte budget, the least important features (smallest projected
footprint first; draw order otherwise preserved) are shed, halving the count
until the tile fits. Deterministic, and measured on the gzipped bytes a PMTiles
range request actually transfers.
"""

from __future__ import annotations

import gzip

import mapbox_vector_tile

from .tms import ZoomGrid


def encode_tile(
    layers: dict[str, list[tuple]],
    col: int,
    row: int,
    zg: ZoomGrid,
    extent: int,
    *,
    max_features: int = 0,
    max_bytes: int = 0,
) -> bytes:
    total = sum(len(feats) for feats in layers.values())
    if max_features and total > max_features:
        layers = _keep_most_important(layers, max_features)
        total = max_features

    data = _encode(layers, zg.tile_bounds(col, row), extent)
    while max_bytes and len(data) > max_bytes and total > 1:
        total //= 2
        layers = _keep_most_important(layers, total)
        data = _encode(layers, zg.tile_bounds(col, row), extent)
    return data


def _encode(
    layers: dict[str, list[tuple]],
    quantize_bounds: tuple[float, float, float, float],
    extent: int,
) -> bytes:
    mvt_layers = [
        {
            "name": name,
            "features": [
                {"geometry": geom, "properties": props} for geom, props in feats
            ],
        }
        for name, feats in layers.items()
        if feats
    ]
    raw = mapbox_vector_tile.encode(
        mvt_layers,
        default_options={
            "quantize_bounds": quantize_bounds,
            "extents": extent,
            "on_invalid_geometry": mapbox_vector_tile.encoder.on_invalid_geometry_make_valid,
        },
    )
    return gzip.compress(raw)


def _keep_most_important(layers: dict[str, list[tuple]], budget: int) -> dict[str, list[tuple]]:
    """Keep the ``budget`` features with the largest projected footprint.

    Importance is the longer side of the geometry's envelope — big shapes carry
    the map, sub-pixel clutter goes first. The sort is stable, so equally sized
    features (all points, notably) are kept in draw order, and survivors are
    re-emitted in their original order to leave layer stacking untouched.
    """
    ranked = sorted(
        (
            (name, idx)
            for name, feats in layers.items()
            for idx in range(len(feats))
        ),
        key=lambda ni: -_footprint(layers[ni[0]][ni[1]][0]),
    )
    keep = set(ranked[:budget])
    return {
        name: [f for idx, f in enumerate(feats) if (name, idx) in keep]
        for name, feats in layers.items()
    }


def _footprint(geom) -> float:
    minx, miny, maxx, maxy = geom.bounds
    return max(maxx - minx, maxy - miny)
