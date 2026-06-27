"""Tests for point clustering / aggregation and the custom Arctic grids."""

from __future__ import annotations

import pytest

from tippykayak import Accumulation, Aggregation, Grid, TileOptions, build_tiles
from tippykayak.aggregate import cluster_points
from tippykayak.features import Feature
from shapely.geometry import Point


def _pt(x, y, **props):
    return Feature(geometry=Point(x, y), properties=props)


def test_custom_arctic_grids_resolve_to_right_epsg():
    g3413 = Grid.named("EPSG3413")
    g3573 = Grid.named("EPSG3573")
    assert g3413.describe()["epsg"] == 3413
    assert g3573.describe()["epsg"] == 3573
    # Square quad → one tile at zoom 0.
    assert g3413.zoom(0).matrix_width == 1
    # EPSG:3573 extent edge sits at ~45N by construction.
    assert g3573.describe()["tile_origin_upper_left_y"] == pytest.approx(4889334.8765)


def test_custom_grids_listed():
    listed = Grid.list_named()
    assert "EPSG3413" in listed
    assert "EPSG3573" in listed


def test_accumulation_parse():
    a = Accumulation.parse("sum:population")
    assert (a.op, a.source, a.out) == ("sum", "population", "population_sum")
    b = Accumulation.parse("mean:temp:avg_temp")
    assert (b.op, b.source, b.out) == ("mean", "temp", "avg_temp")
    with pytest.raises(ValueError):
        Accumulation.parse("bogus:field")


def test_cluster_merges_and_accumulates():
    agg = Aggregation(enabled=True, accumulate=(Accumulation.parse("sum:pop"),))
    points = [_pt(0, 0, pop=10, name="a"), _pt(1, 1, pop=5, name="b"), _pt(500, 500, pop=7, name="c")]
    # Cell of 100 groups the first two; the third is its own cluster.
    clusters = cluster_points(points, cell_size=100.0, agg=agg)
    assert len(clusters) == 2
    by_count = sorted(c.properties["point_count"] for c in clusters)
    assert by_count == [1, 2]
    merged = next(c for c in clusters if c.properties["point_count"] == 2)
    assert merged.properties["pop_sum"] == 15


def test_weighted_centroid_pulls_toward_heavy_point():
    agg = Aggregation(enabled=True, weight_property="population")
    points = [_pt(0, 0, population=1), _pt(10, 0, population=99)]
    [cluster] = cluster_points(points, cell_size=100.0, agg=agg)
    # Centre of mass is near the heavy point (x≈9.9), not the midpoint (5).
    assert cluster.geometry.x > 9.0


def test_unweighted_centroid_is_midpoint():
    agg = Aggregation(enabled=True)
    points = [_pt(0, 0, population=1), _pt(10, 0, population=99)]
    [cluster] = cluster_points(points, cell_size=100.0, agg=agg)
    assert cluster.geometry.x == pytest.approx(5.0)


def test_clustering_conserves_point_count_across_zoom():
    grid = Grid.named("EPSG3413")
    # A dense blob of points near 80N.
    feats = []
    for i in range(50):
        feats.append(_pt(*_proj(grid, -45 + (i % 7) * 0.5, 80 + (i // 7) * 0.2)))
    opts = TileOptions(layer="t", min_zoom=0, max_zoom=5, aggregation=Aggregation(enabled=True, distance_pixels=30))
    pyramid = build_tiles(grid, feats, opts)

    def total_count_at(z):
        return sum(
            props.get("point_count", 0)
            for (zz, _, _), layers in pyramid.items() if zz == z
            for feats_list in layers.values()
            for _geom, props in feats_list
        )

    # Every original point is represented at every zoom (no loss).
    for z in range(0, 6):
        assert total_count_at(z) == 50


def _proj(grid, lon, lat):
    from pyproj import CRS, Transformer

    tr = Transformer.from_crs(CRS.from_epsg(4326), grid.crs, always_xy=True)
    return tr.transform(lon, lat)
