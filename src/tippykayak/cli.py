"""Command-line interface: ``tippykayak input.geojson output.pmtiles --tms ...``."""

from __future__ import annotations

import argparse
import sys

from .aggregate import Accumulation, Aggregation
from .pipeline import build
from .tiler import TileOptions
from .tms import Grid


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tippykayak",
        description="Generate non-WebMercator PMTiles on a morecantile TileMatrixSet.",
    )
    p.add_argument(
        "input",
        nargs="?",
        help="Input file: GeoJSON, or an OpenStreetMap/Geofabrik .osm.pbf "
        "(format auto-detected by extension).",
    )
    p.add_argument("output", nargs="?", help="Output .pmtiles path.")
    p.add_argument(
        "--tms",
        default="WorldCRS84Quad",
        help="TileMatrixSet id (default: WorldCRS84Quad). Use --list-tms to see options.",
    )
    p.add_argument("--list-tms", action="store_true", help="List available TileMatrixSets and exit.")
    p.add_argument("--layer", default="tippykayak", help="MVT layer name.")
    p.add_argument("--name", default="tippykayak", help="Tileset name stored in metadata.")
    p.add_argument("--minzoom", type=int, default=0)
    p.add_argument("--maxzoom", type=int, default=6)
    p.add_argument("--input-crs", default="4326", help="CRS of GeoJSON input (default 4326). Ignored for .osm.pbf, which is always EPSG:4326.")

    osm = p.add_argument_group("OpenStreetMap input (.osm.pbf)")
    osm.add_argument(
        "--theme",
        default=None,
        metavar="THEME.json",
        help="JSON theme overriding the built-in general-basemap tag→class mapping.",
    )
    osm.add_argument(
        "--bbox",
        default=None,
        metavar="MINLON,MINLAT,MAXLON,MAXLAT",
        help="Only keep OSM features intersecting this lon/lat box (trims large extracts).",
    )
    p.add_argument("--extent", type=int, default=4096, help="MVT tile extent.")
    p.add_argument("--simplify-pixels", type=float, default=1.0, help="Douglas-Peucker tolerance in pixels.")
    p.add_argument("--min-feature-pixels", type=float, default=1.5, help="Drop features smaller than this (px). 0 disables.")
    p.add_argument("--point-retain", type=float, default=1.0, help="Per-zoom point retention factor (1.0 = keep all). Ignored when --cluster is set.")
    p.add_argument("--buffer-pixels", type=float, default=8.0, help="Tile edge buffer in pixels.")

    agg = p.add_argument_group("point aggregation (clustering)")
    agg.add_argument("--cluster", action="store_true", help="Cluster nearby points into counted representatives.")
    agg.add_argument("--cluster-distance", type=float, default=32.0, help="Cluster cell size in pixels (default 32).")
    agg.add_argument("--cluster-count-property", default="point_count", help="Property holding the cluster size.")
    agg.add_argument("--cluster-weight", default=None, metavar="FIELD", help="Weight cluster centroids by this numeric field (centre of mass).")
    agg.add_argument(
        "--accumulate",
        action="append",
        default=[],
        metavar="OP:FIELD[:OUT]",
        help="Accumulate a numeric field over each cluster. OP is sum|mean|min|max|count. Repeatable.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_tms:
        for name in Grid.list_named():
            print(name)
        return 0

    if not args.input or not args.output:
        build_parser().error("input and output are required (unless --list-tms)")

    grid = Grid.named(args.tms)

    theme = None
    if args.theme:
        from .osm import load_theme

        theme = load_theme(args.theme)

    bbox = None
    if args.bbox:
        parts = [float(v) for v in args.bbox.split(",")]
        if len(parts) != 4:
            build_parser().error("--bbox expects MINLON,MINLAT,MAXLON,MAXLAT")
        bbox = (parts[0], parts[1], parts[2], parts[3])

    aggregation = Aggregation(
        enabled=args.cluster,
        distance_pixels=args.cluster_distance,
        count_property=args.cluster_count_property,
        weight_property=args.cluster_weight,
        accumulate=tuple(Accumulation.parse(spec) for spec in args.accumulate),
    )
    options = TileOptions(
        layer=args.layer,
        min_zoom=args.minzoom,
        max_zoom=args.maxzoom,
        extent=args.extent,
        simplify_pixels=args.simplify_pixels,
        min_feature_pixels=args.min_feature_pixels,
        point_retain_per_zoom=args.point_retain,
        buffer_pixels=args.buffer_pixels,
        aggregation=aggregation,
    )

    try:
        result = build(
            args.input,
            args.output,
            grid,
            options,
            input_crs=args.input_crs,
            name=args.name,
            theme=theme,
            bbox=bbox,
        )
    except ValueError as exc:
        print(f"tippykayak: {exc}", file=sys.stderr)
        return 1

    print(
        f"Wrote {result.output} — {result.tile_count} tiles, "
        f"{result.feature_count} features, grid {result.grid}, "
        f"z{result.min_zoom}-{result.max_zoom}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
