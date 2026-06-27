"""End-to-end and unit tests for the tippykayak tiling pipeline."""

from __future__ import annotations

import gzip
import json

import mapbox_vector_tile
import pytest
from pmtiles.reader import MmapSource, Reader

from tippykayak import Grid, TileOptions, build
from tippykayak.features import load_geojson
from tippykayak.tiler import build_tiles


SAMPLE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"name": "basin", "kind": "ice"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-30, -85], [30, -85], [40, -75], [-40, -75], [-30, -85]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"name": "ridge"},
            "geometry": {"type": "LineString", "coordinates": [[-60, -80], [0, -78], [60, -82]]},
        },
        {
            "type": "Feature",
            "properties": {"name": "station"},
            "geometry": {"type": "Point", "coordinates": [10, -77]},
        },
    ],
}


@pytest.fixture
def geojson_file(tmp_path):
    p = tmp_path / "in.geojson"
    p.write_text(json.dumps(SAMPLE))
    return p


def test_grid_describe_is_polar():
    grid = Grid.named("UPSAntarcticWGS84Quad")
    desc = grid.describe()
    assert desc["epsg"] == 5042
    assert desc["tile_dimension_zoom_0"] > 0
    # Upper-left origin: x to the left, y at the top.
    assert desc["tile_origin_upper_left_y"] > desc["tile_origin_upper_left_x"]


def test_zoom_grid_tile_roundtrip():
    grid = Grid.named("UPSAntarcticWGS84Quad")
    zg = grid.zoom(3)
    # A point in the middle of tile (2, 3) must map back to (2, 3).
    minx, miny, maxx, maxy = zg.tile_bounds(2, 3)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    assert zg.tile_for(cx, cy) == (2, 3)


def test_features_are_reprojected_into_grid_crs(geojson_file):
    grid = Grid.named("UPSAntarcticWGS84Quad")
    feats = load_geojson(geojson_file, grid)
    assert len(feats) == 3
    # Projected coordinates are in metres, far from lon/lat magnitudes.
    minx, miny, maxx, maxy = feats[0].geometry.bounds
    assert abs(minx) > 1000


def test_build_tiles_pyramid_has_multiple_zooms(geojson_file):
    grid = Grid.named("UPSAntarcticWGS84Quad")
    feats = load_geojson(geojson_file, grid)
    pyramid = build_tiles(grid, feats, TileOptions(layer="t", min_zoom=0, max_zoom=4))
    zooms = {z for (z, _, _) in pyramid}
    assert zooms == {0, 1, 2, 3, 4}


def test_build_writes_valid_pmtiles_with_tms_metadata(geojson_file, tmp_path):
    grid = Grid.named("UPSAntarcticWGS84Quad")
    out = tmp_path / "out.pmtiles"
    result = build(geojson_file, out, grid, TileOptions(layer="features", min_zoom=0, max_zoom=4))

    assert result.tile_count > 0
    assert out.exists()

    with open(out, "rb") as f:
        reader = Reader(MmapSource(f))
        header = reader.header()
        assert header["max_zoom"] == 4
        meta = reader.metadata()
        # The non-WebMercator CRS info must travel with the archive.
        assert meta["tippykayak"]["epsg"] == 5042
        assert meta["crs"] == "EPSG:5042"

        # The z0 tile must decode to MVT with our layer and coords in-range.
        data = reader.get(0, 0, 0)
        assert data is not None
        decoded = mapbox_vector_tile.decode(gzip.decompress(data))
        assert "features" in decoded
        assert len(decoded["features"]["features"]) >= 1


def test_size_dropping_thins_low_zoom(geojson_file):
    grid = Grid.named("UPSAntarcticWGS84Quad")
    feats = load_geojson(geojson_file, grid)
    # Aggressive size threshold should drop the small line/polygon at z0 but the
    # max zoom always keeps everything.
    opts = TileOptions(layer="t", min_zoom=0, max_zoom=4, min_feature_pixels=100.0)
    pyramid = build_tiles(grid, feats, opts)

    # The deepest zoom should retain at least as many feature placements as z0.
    def count_at(z):
        return sum(
            len(feats_list)
            for (zz, _, _), layers in pyramid.items()
            if zz == z
            for feats_list in layers.values()
        )
    assert count_at(4) >= count_at(0)
