"""Tests for OpenStreetMap / Geofabrik ``.osm.pbf`` input."""

from __future__ import annotations

import gzip

import mapbox_vector_tile
import osmium
import osmium.osm.mutable as om
import pytest
from pmtiles.reader import MmapSource, Reader

from tippykayak import Grid, TileOptions, build
from tippykayak.features import load_features
from tippykayak.osm import DEFAULT_THEME, Rule, iter_osm_raw


@pytest.fixture
def osm_pbf(tmp_path):
    """A tiny synthetic extract with one feature of each geometry kind.

    A ``place`` node (point), a ``waterway`` way (line) and a closed
    ``natural=water`` way (area), plus a ``building`` — exercising the point,
    line and area branches of the reader.
    """
    path = tmp_path / "sample.osm.pbf"
    writer = osmium.SimpleWriter(str(path))

    # Place node.
    writer.add_node(om.Node(id=1, location=(10.0, 50.0),
                            tags={"place": "town", "name": "Testville"}))

    # River centreline (line).
    writer.add_node(om.Node(id=2, location=(10.0, 50.0)))
    writer.add_node(om.Node(id=3, location=(10.1, 50.1)))
    writer.add_way(om.Way(id=1, nodes=[2, 3],
                          tags={"waterway": "river", "name": "Test River"}))

    # Lake (closed way -> area).
    lake = [(11.0, 51.0), (11.2, 51.0), (11.2, 51.2), (11.0, 51.2), (11.0, 51.0)]
    for i, (lon, lat) in enumerate(lake, start=10):
        writer.add_node(om.Node(id=i, location=(lon, lat)))
    writer.add_way(om.Way(id=2, nodes=[10, 11, 12, 13, 10],
                          tags={"natural": "water", "name": "Test Lake"}))

    # Building (closed way -> area).
    bld = [(12.0, 52.0), (12.001, 52.0), (12.001, 52.001), (12.0, 52.001), (12.0, 52.0)]
    for i, (lon, lat) in enumerate(bld, start=20):
        writer.add_node(om.Node(id=i, location=(lon, lat)))
    writer.add_way(om.Way(id=3, nodes=[20, 21, 22, 23, 20],
                          tags={"building": "yes"}))

    writer.close()
    return path


def test_iter_osm_raw_classifies_each_geometry_kind(osm_pbf):
    raw = list(iter_osm_raw(osm_pbf))
    classes = {props["class"] for _, props, _ in raw}
    assert classes == {"place", "waterway", "water", "building"}

    by_class = {props["class"]: (geom, props) for geom, props, _ in raw}
    assert by_class["place"][0].geom_type == "Point"
    assert by_class["waterway"][0].geom_type == "LineString"
    # An area is delivered as a (multi)polygon, never the ring line.
    assert by_class["water"][0].geom_type in ("Polygon", "MultiPolygon")
    # Pass-through name and the matched tag value as subclass.
    assert by_class["place"][1]["name"] == "Testville"
    assert by_class["waterway"][1]["subclass"] == "river"


def test_untagged_helper_nodes_are_skipped(osm_pbf):
    # The way/area nodes carry no tags and must not become features.
    raw = list(iter_osm_raw(osm_pbf))
    assert len(raw) == 4


def test_bbox_filters_features(osm_pbf):
    # A box around the lake/building only (lon >= 11) drops the place + river.
    raw = list(iter_osm_raw(osm_pbf, bbox=(10.5, 50.5, 13.0, 53.0)))
    classes = {props["class"] for _, props, _ in raw}
    assert classes == {"water", "building"}


def test_custom_theme_narrows_extraction(osm_pbf):
    only_water = (Rule("water", "area", "natural", frozenset({"water"})),)
    raw = list(iter_osm_raw(osm_pbf, theme=only_water))
    assert {props["class"] for _, props, _ in raw} == {"water"}


def test_load_features_dispatches_on_extension(osm_pbf):
    grid = Grid.named("WorldCRS84Quad")
    feats = load_features(osm_pbf, grid)
    assert len(feats) == 4
    # Reprojected into the grid CRS (WorldCRS84Quad is degrees, so still lon/lat
    # magnitudes here — but the dispatch + projection path is exercised).
    assert all(f.geometry.is_valid for f in feats)


def test_build_pmtiles_from_osm(osm_pbf, tmp_path):
    # A geographic grid (the synthetic data sits in Europe), tiled in degrees to
    # prove OSM input is grid-agnostic. CRS84Square is the PMTiles-addressable
    # (square 2^z quad) geographic scheme — plain WorldCRS84Quad is 2×1 at z0.
    grid = Grid.named("CRS84Square")
    out = tmp_path / "osm.pmtiles"
    result = build(osm_pbf, out, grid, TileOptions(layer="osm", min_zoom=0, max_zoom=6))

    assert result.feature_count == 4
    assert result.tile_count > 0
    assert out.exists()

    with open(out, "rb") as f:
        reader = Reader(MmapSource(f))
        meta = reader.metadata()
        # The grid's CRS travels with the archive (no CRS field in the header).
        assert meta["tippykayak"]["tilematrixset"] == "CRS84Square"
        assert meta["tippykayak"]["proj4"].startswith("+proj=longlat")
        # The single OSM layer declares the synthesised class/subclass fields.
        fields = meta["vector_layers"][0]["fields"]
        assert "class" in fields and "subclass" in fields

        data = reader.get(0, 0, 0)
        assert data is not None
        decoded = mapbox_vector_tile.decode(gzip.decompress(data))
        assert "osm" in decoded


def test_default_theme_is_ordered_specific_before_catchall():
    # The reservoir→water rule must precede the open-ended landuse rule so a
    # landuse=reservoir polygon becomes water, not landuse.
    landuse_rules = [r for r in DEFAULT_THEME if r.geometry == "area" and r.key == "landuse"]
    specific = next(r for r in landuse_rules if r.values and "reservoir" in r.values)
    catchall = next(r for r in landuse_rules if r.values is None)
    assert DEFAULT_THEME.index(specific) < DEFAULT_THEME.index(catchall)
