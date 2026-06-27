"""Encode a tile's projected geometry into a gzipped MVT blob.

The key detail: we hand ``mapbox_vector_tile`` the geometry in *CRS units* and a
``quantize_bounds`` equal to the tile's exact CRS bounds. The encoder then scales
into the 0..extent tile grid and flips the y axis. Geometry inside the buffer
falls slightly outside 0..extent, which is exactly what MVT clients expect.
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
) -> bytes:
    quantize_bounds = zg.tile_bounds(col, row)
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
