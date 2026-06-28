"""Write encoded tiles into a PMTiles archive with TMS-aware metadata.

PMTiles headers have no CRS field — the format assumes the reader already knows
the tiling scheme. For a non-WebMercator archive that assumption is dangerous, so
tippykayak embeds the full TileMatrixSet description in the metadata JSON under a
``tippykayak`` key (plus ``crs`` / ``tile_origin`` / ``tile_dimension_zoom_0`` at
the top level). The OpenLayers viewer reads exactly this to configure proj4 and a
matching tile grid.
"""

from __future__ import annotations

from pathlib import Path

from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from .tms import Grid


def write_pmtiles(
    path: str | Path,
    tiles: dict[tuple[int, int, int], bytes],
    grid: Grid,
    *,
    min_zoom: int,
    max_zoom: int,
    vector_layers: list[dict],
    geographic_bounds: tuple[float, float, float, float],
    data_extent: tuple[float, float, float, float] | None = None,
    name: str = "tippykayak",
) -> None:
    min_lon, min_lat, max_lon, max_lat = geographic_bounds

    # Clustered archives require tiles in ascending Hilbert tile-id order.
    ordered = sorted(
        ((zxy_to_tileid(z, x, y), data) for (z, x, y), data in tiles.items()),
        key=lambda kv: kv[0],
    )

    # The grid block (CRS + tiling), plus the data's own projected extent so a
    # client can frame the data exactly without re-projecting lon/lat (which is
    # lossy for conic/azimuthal CRSs).
    tippykayak_meta = grid.describe()
    if data_extent is not None:
        tippykayak_meta["data_extent"] = list(data_extent)

    metadata = {
        "name": name,
        "type": "overlay",
        "format": "pbf",
        "minzoom": min_zoom,
        "maxzoom": max_zoom,
        "bounds": [min_lon, min_lat, max_lon, max_lat],
        "vector_layers": vector_layers,
        # Non-WebMercator clients read this block to know how to place the tiles.
        "tippykayak": tippykayak_meta,
        **{
            k: grid.describe()[k]
            for k in ("crs", "tile_origin_upper_left_x", "tile_origin_upper_left_y", "tile_dimension_zoom_0")
        },
    }

    with open(path, "wb") as f:
        writer = Writer(f)
        for tileid, data in ordered:
            writer.write_tile(tileid, data)
        header = {
            "tile_type": TileType.MVT,
            "tile_compression": Compression.GZIP,
            "min_lon_e7": int(min_lon * 1e7),
            "min_lat_e7": int(min_lat * 1e7),
            "max_lon_e7": int(max_lon * 1e7),
            "max_lat_e7": int(max_lat * 1e7),
            "center_zoom": min_zoom,
            "center_lon_e7": int((min_lon + max_lon) / 2 * 1e7),
            "center_lat_e7": int((min_lat + max_lat) / 2 * 1e7),
        }
        writer.finalize(header, metadata)
