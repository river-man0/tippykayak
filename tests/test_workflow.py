"""Tests for the tippecanoe-inspired workflow: quadtree descent, guessed
maxzoom, polygon dust, and per-tile budgets."""

from __future__ import annotations

import gzip
import json

import mapbox_vector_tile
import pytest
from pmtiles.reader import MmapSource, Reader

from tippykayak import Grid, TileOptions, build, guess_max_zoom
from tippykayak.features import iter_features
from tippykayak.tiler import build_tiles


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def polygon(coords, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Polygon", "coordinates": [coords]},
    }


def point(lon, lat, **props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


# ---------------------------------------------------------------------------
# Quadtree descent
# ---------------------------------------------------------------------------


def test_descent_matches_direct_clipping_tile_for_tile():
    """The descent must place geometry in exactly the tiles a direct
    clip-every-zoom approach would: every tile's clip window (bounds + buffer)
    must intersect the source, and every touched tile must be present."""
    grid = Grid.named("EPSG3413")
    src = fc([
        polygon([[-60, 70], [-30, 70], [-30, 80], [-60, 80], [-60, 70]], kind="land"),
        {
            "type": "Feature",
            "properties": {"kind": "route"},
            "geometry": {
                "type": "LineString",
                "coordinates": [[-100 + i, 65 + 10 * (i % 2) / 10] for i in range(60)],
            },
        },
    ])
    feats = list(iter_features(src, grid))
    opts = TileOptions(layer="t", min_zoom=0, max_zoom=6, min_feature_pixels=0)
    pyramid = build_tiles(grid, feats, opts)

    for z in range(opts.min_zoom, opts.max_zoom + 1):
        zg = grid.zoom(z)
        buffer_crs = opts.buffer_pixels * zg.resolution
        expected = set()
        for f in feats:
            minx, miny, maxx, maxy = f.geometry.buffer(0).bounds
            for col in range(zg.matrix_width):
                for row in range(zg.matrix_height):
                    tminx, tminy, tmaxx, tmaxy = zg.tile_bounds(col, row)
                    window = (
                        tminx - buffer_crs, tminy - buffer_crs,
                        tmaxx + buffer_crs, tmaxy + buffer_crs,
                    )
                    from shapely.geometry import box

                    if f.geometry.intersects(box(*window)):
                        expected.add((z, col, row))
        got = {k for k in pyramid if k[0] == z}
        assert got == expected


def test_descent_geometry_stays_within_buffered_tile_window():
    grid = Grid.named("EPSG3573")
    src = fc([polygon([[-170, 45], [170, 45], [170, 85], [-170, 85], [-170, 45]], kind="cap")])
    feats = list(iter_features(src, grid))
    opts = TileOptions(layer="t", min_zoom=0, max_zoom=5)
    pyramid = build_tiles(grid, feats, opts)
    assert pyramid
    for (z, col, row), layers in pyramid.items():
        zg = grid.zoom(z)
        pad = opts.buffer_pixels * zg.resolution * 1.001
        tminx, tminy, tmaxx, tmaxy = zg.tile_bounds(col, row)
        for feats_list in layers.values():
            for geom, _ in feats_list:
                gminx, gminy, gmaxx, gmaxy = geom.bounds
                assert gminx >= tminx - pad and gmaxx <= tmaxx + pad
                assert gminy >= tminy - pad and gmaxy <= tmaxy + pad


# ---------------------------------------------------------------------------
# guess_max_zoom (tippecanoe's -zg)
# ---------------------------------------------------------------------------


def test_guess_max_zoom_from_segment_spacing_is_exact():
    # A line whose vertices are spaced exactly 0.01° on the CRS84 square grid.
    # The target step is an eighth of the (geometric) mean segment length —
    # tippecanoe's constant — i.e. 0.00125°. The quantization step is
    # 360 / (2^z * 4096) degrees, and the first zoom at or below the target is
    # z=7 (2^7 = 128 >= 360 / (0.00125 * 4096) ≈ 70.3; z=6 gives 64).
    grid = Grid.named("CRS84Square")
    line = {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "LineString",
            "coordinates": [[i * 0.01, 40.0] for i in range(500)],
        },
    }
    feats = list(iter_features(fc([line]), grid))
    z = guess_max_zoom(grid, feats, TileOptions(max_zoom=None))
    assert z == 7


def test_guess_max_zoom_deepens_with_density():
    grid = Grid.named("EPSG3413")
    import random

    random.seed(7)

    def cloud(spread):
        return list(iter_features(
            fc([point(-60 + random.uniform(0, spread), 70 + random.uniform(0, spread / 2))
                for _ in range(800)]),
            grid,
        ))

    sparse = guess_max_zoom(grid, cloud(30.0), TileOptions(max_zoom=None))
    dense = guess_max_zoom(grid, cloud(0.3), TileOptions(max_zoom=None))
    assert dense > sparse
    assert 0 <= sparse <= dense <= grid.max_zoom


def test_guess_max_zoom_respects_explicit_value_and_empty_input():
    grid = Grid.named("EPSG3413")
    assert guess_max_zoom(grid, [], TileOptions(max_zoom=9)) == 9
    # Nothing to measure: fall back to the historical default, floored at minzoom.
    assert guess_max_zoom(grid, [], TileOptions(max_zoom=None)) == 6
    assert guess_max_zoom(grid, [], TileOptions(max_zoom=None, min_zoom=8)) == 8


def test_build_resolves_auto_maxzoom(tmp_path):
    src = tmp_path / "pts.geojson"
    src.write_text(json.dumps(fc([point(-60 + i * 0.05, 75, id=i) for i in range(50)])))
    grid = Grid.named("EPSG3413")
    result = build(src, tmp_path / "out.pmtiles", grid,
                   TileOptions(layer="pts", min_zoom=0, max_zoom=None))
    assert isinstance(result.max_zoom, int)
    assert result.max_zoom >= result.min_zoom
    with open(tmp_path / "out.pmtiles", "rb") as f:
        assert Reader(MmapSource(f)).header()["max_zoom"] == result.max_zoom


# ---------------------------------------------------------------------------
# Polygon dust
# ---------------------------------------------------------------------------


def _tiny_polygon_town(grid):
    # 400 sub-pixel "buildings" (~150 m x 450 m) clustered near 60°W 75°N.
    feats = []
    for i in range(400):
        x = -60 + 0.01 * (i % 20) + 0.2 * (i // 20)
        feats.append(polygon(
            [[x, 75], [x + 0.005, 75], [x + 0.005, 75.004], [x, 75.004], [x, 75]],
            kind="bldg",
        ))
    return list(iter_features(fc(feats), grid))


def count_at(pyramid, z):
    return sum(
        len(feats_list)
        for (zz, _, _), layers in pyramid.items()
        if zz == z
        for feats_list in layers.values()
    )


def test_polygon_dust_preserves_visual_mass_of_dropped_polygons():
    grid = Grid.named("EPSG3413")
    feats = _tiny_polygon_town(grid)
    opts = dict(layer="t", min_zoom=0, max_zoom=8)

    dusted = build_tiles(grid, feats, TileOptions(**opts, polygon_dust=True))
    bare = build_tiles(grid, feats, TileOptions(**opts, polygon_dust=False))

    # Without dust the whole town vanishes below its visibility zoom; with dust
    # a few placeholder squares stand in for the accumulated area — more of
    # them at each deeper zoom, but always far fewer than the source polygons.
    assert count_at(bare, 5) == 0
    assert 0 < count_at(dusted, 5) < 400
    assert count_at(dusted, 5) <= count_at(dusted, 6) <= count_at(dusted, 7)
    # At maxzoom nothing is dropped, so dust changes nothing there.
    assert count_at(dusted, 8) == count_at(bare, 8)


def test_dust_squares_carry_source_properties_and_pixel_size():
    grid = Grid.named("EPSG3413")
    feats = _tiny_polygon_town(grid)
    opts = TileOptions(layer="t", min_zoom=0, max_zoom=8)
    pyramid = build_tiles(grid, feats, opts)
    zg = grid.zoom(5)
    side = opts.min_feature_pixels * zg.resolution
    dust = [
        (geom, props)
        for (z, _, _), layers in pyramid.items()
        if z == 5
        for fl in layers.values()
        for geom, props in fl
    ]
    assert dust
    for geom, props in dust:
        assert props["kind"] == "bldg"
        minx, miny, maxx, maxy = geom.bounds
        assert maxx - minx == pytest.approx(side)
        assert maxy - miny == pytest.approx(side)


# ---------------------------------------------------------------------------
# Per-tile budgets
# ---------------------------------------------------------------------------


def test_max_tile_features_caps_every_tile(tmp_path):
    src = tmp_path / "pts.geojson"
    src.write_text(json.dumps(fc([
        point(-60 + (i % 10) * 0.01, 75 + (i // 10) * 0.01, id=i) for i in range(60)
    ])))
    grid = Grid.named("EPSG3413")
    out = tmp_path / "out.pmtiles"
    build(src, out, grid,
          TileOptions(layer="pts", min_zoom=0, max_zoom=3, max_tile_features=10))
    with open(out, "rb") as f:
        reader = Reader(MmapSource(f))
        data = reader.get(0, 0, 0)
        decoded = mapbox_vector_tile.decode(gzip.decompress(data))
        assert len(decoded["pts"]["features"]) == 10


def test_max_tile_bytes_sheds_smallest_features_until_tiles_fit(tmp_path):
    # Fat string properties make the z0 tile bust a small byte budget.
    src = tmp_path / "pts.geojson"
    src.write_text(json.dumps(fc([
        point(-60 + (i % 20) * 0.05, 75 + (i // 20) * 0.05,
              id=i, blurb=f"station-{i:04d}-" + "x" * 60)
        for i in range(300)
    ])))
    grid = Grid.named("EPSG3413")
    budget = 2000
    out = tmp_path / "out.pmtiles"
    build(src, out, grid,
          TileOptions(layer="pts", min_zoom=0, max_zoom=2, max_tile_bytes=budget))
    with open(out, "rb") as f:
        reader = Reader(MmapSource(f))
        for z in range(3):
            for x in range(2 ** z):
                for y in range(2 ** z):
                    data = reader.get(z, x, y)
                    if data is None:
                        continue
                    assert len(data) <= budget
                    # Still a valid, non-empty tile after shedding.
                    decoded = mapbox_vector_tile.decode(gzip.decompress(data))
                    assert decoded["pts"]["features"]


def test_budget_sheds_smallest_shapes_first():
    from tippykayak.encode import _keep_most_important

    from shapely.geometry import box as sbox

    layers = {
        "t": [
            (sbox(0, 0, 1, 1), {"id": "small"}),
            (sbox(0, 0, 100, 100), {"id": "big"}),
            (sbox(0, 0, 10, 10), {"id": "mid"}),
        ]
    }
    kept = _keep_most_important(layers, 2)
    ids = [props["id"] for _, props in kept["t"]]
    # The two largest survive, in their original draw order.
    assert ids == ["big", "mid"]
