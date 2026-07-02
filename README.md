# tippykayak

**Non-WebMercator PMTiles, built on [morecantile](https://developmentseed.org/morecantile/) TileMatrixSets.**

Most vector-tile tooling only ever emits Web Mercator. tippykayak tiles GeoJSON
or OpenStreetMap data onto **any** OGC TileMatrixSet — polar, conic, or
geographic — and writes one static `.pmtiles` file that any range-capable web
host can serve, no tile server needed. Simplification, dropping and clustering
all happen natively in the projected space of the grid you choose, so the
output is correct in CRSs where Web Mercator is wrong: the Arctic
(EPSG:3413 / EPSG:3573), Canada Atlas Lambert (EPSG:3978), and beyond.

![Circumpolar land re-tiled across four projections, rendered in OpenLayers](viewer/preview.png)

## Quick start

```bash
git clone https://github.com/river-man0/tippykayak && cd tippykayak
pip install -e .

# 1. Tile your GeoJSON onto a polar grid; let the data pick the maxzoom
tippykayak data.geojson out.pmtiles --tms EPSG3413 --maxzoom auto

# 2. Serve with HTTP Range support (PMTiles needs it; stock http.server lacks it)
python serve.py

# 3. Open the viewer — it configures itself from the archive's embedded metadata
#    http://localhost:8000/viewer/index.html?src=../out.pmtiles
```

Or run the demo — one Natural Earth dataset tiled onto **five** projections
with a live switcher ([hosted copy](https://river-man0.github.io/tippykayak/)):

```bash
pip install -e '.[examples]'
python examples/make_projections.py
python serve.py     # then open http://localhost:8000/viewer/index.html
```

## What it does

| Capability | |
| --- | --- |
| Any TileMatrixSet | morecantile's defaults, tippykayak's polar/conic grids, or your own `Grid.custom(...)` — the reason the project exists |
| Quadtree tiler | clip once, split tiles into their 4 children; cost proportional to local detail |
| Simplify | Douglas-Peucker per tile, tolerance scaled to each zoom's resolution |
| Drop | size-based switch-on zoom for shapes; deterministic, zoom-stable dot-dropping for points |
| Polygon dust | dropped sub-pixel polygons accumulate into placeholder squares, so dense fields keep their mass at low zoom |
| Cluster | grid clustering with `point_count` + sum/mean/min/max accumulation, conserved across zooms |
| Auto maxzoom | `--maxzoom auto` picks the shallowest zoom that resolves the data's spacing (tippecanoe's `-zg`) |
| Tile budgets | hard per-tile byte/feature caps; oversized tiles shed their smallest features until they fit |
| Inputs | GeoJSON and OpenStreetMap / Geofabrik `.osm.pbf` |

The workflow adapts the ideas that make [tippecanoe](https://github.com/felt/tippecanoe)
the reference tool for this job, re-derived in each grid's own CRS units —
see [`docs/tippecanoe.md`](docs/tippecanoe.md). The research behind the
architecture (why OpenLayers, why embedded CRS metadata, why native tiling)
is in [`docs/design.md`](docs/design.md).

## CLI

```bash
tippykayak --list-tms            # every available grid (custom ones first)
tippykayak INPUT OUTPUT.pmtiles --tms GRID [options]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--tms` | `WorldCRS84Quad` | TileMatrixSet id (`EPSG3413`, `EPSG3573`, `EPSG3978`, `CRS84Square`, any morecantile id) |
| `--minzoom` / `--maxzoom` | 0 / 6 | zoom range; `--maxzoom auto` guesses from the data |
| `--layer`, `--name` | `tippykayak` | MVT layer name / tileset name |
| `--simplify-pixels` | 1.0 | Douglas-Peucker tolerance, in pixels |
| `--min-feature-pixels` | 1.5 | drop features smaller than this; 0 disables |
| `--no-polygon-dust` | off | discard sub-pixel polygons instead of emitting dust squares |
| `--point-retain` | 1.0 | per-zoom point retention (0.5 ≈ half the points per zoom out) |
| `--max-tile-bytes` / `--max-tile-features` | 512000 / 200000 | per-tile budgets; 0 disables |
| `--buffer-pixels` | 8.0 | tile edge buffer |
| `--input-crs` | 4326 | CRS of GeoJSON input |

### OpenStreetMap input

Point tippykayak at a [Geofabrik](https://download.geofabrik.de/) extract (auto-detected
by the `.osm.pbf` extension) and it tiles onto whatever grid you choose — the
data is reprojected exactly like GeoJSON, so polar and conic grids work too:

```bash
tippykayak iceland.osm.pbf ice.pmtiles --tms EPSG3573 --maxzoom 10 --bbox -25,63,-13,67
```

A **theme** maps OSM's free-form tags to a single tile-friendly layer where each
feature carries `class` / `subclass` / `name` (built-in classes: `coastline`,
`waterway`, `road`, `place`, `water`, `wetland`, `glacier`, `landuse`,
`building`). Override with `--theme my-theme.json` — a list of
`{"class","geometry","key","values"?}` rules, first match wins.

> **Memory:** the whole input is held in memory. Use regional Geofabrik
> extracts (or `--bbox`), not a planet file.

### Point clustering

Instead of dropping points as you zoom out, merge them into representatives
that carry a count (and accumulated attributes) — counts are conserved at
every zoom, and clusters split apart as you zoom in:

```bash
tippykayak settlements.geojson out.pmtiles --tms EPSG3413 \
  --cluster --cluster-distance 44 \
  --cluster-weight population --accumulate sum:population
```

Each `--accumulate OP:FIELD[:OUT]` adds an aggregated field
(`sum|mean|min|max|count`; repeatable). `--cluster-weight FIELD` places each
cluster at its centre of mass weighted by that field.

## Python API

```python
from tippykayak import Grid, TileOptions, build, Aggregation, Accumulation

grid = Grid.named("EPSG3573")                      # or any morecantile id
# grid = Grid.custom(3031, [-4194304, -4194304, 4194304, 4194304], "Antarctic")

build("settlements.geojson", "out.pmtiles", grid,
      TileOptions(
          layer="arctic", min_zoom=0, max_zoom=None,   # None = guess (-zg)
          aggregation=Aggregation(
              enabled=True, distance_pixels=36,
              accumulate=(Accumulation.parse("sum:population"),),
          ),
      ))
```

## The viewer

`viewer/` is a reusable OpenLayers front-end for non-WebMercator PMTiles. It
opens *any* tippykayak archive and configures itself from the embedded
metadata — proj4 string, tile grid origin and zoom-0 span, CRS extent, layer
list — so an archive in a projection it has never seen just works. Open an
archive via the projection switcher, a remote URL (needs CORS + HTTP Range), a
local file / drag-and-drop, or `?src=…`.

The pre-built, CDN-free bundle is committed in `viewer/dist/`; after editing
`viewer/src/main.js`, rebuild with `npm install && npm run build:viewer`.

> **Serving:** PMTiles is read with HTTP **Range** requests. `python serve.py`
> adds Range support (Python's stock server doesn't); `npx http-server`, GitHub
> Pages, or any range-capable host works too.

## How it works

```
GeoJSON / .osm.pbf ── classify (OSM theme) ── reproject into the grid's CRS
   ▼
quadtree descent: clip once, split each tile into its 4 children
   ├─ simplify per tile · drop by size · polygon dust · cluster/dot-drop points
   ├─ enforce per-tile byte + feature budgets
   ▼
gzipped MVT per tile ── PMTiles archive + embedded TileMatrixSet metadata
   ▼
OpenLayers viewer (proj4 projection + matching TileGrid, self-configured)
```

Every decision is made in the grid's own CRS units (metres or degrees, from
each matrix's `cellSize`) — never in Web Mercator.

## Layout

```
src/tippykayak/
  tms.py        grid math on morecantile + custom grids (EPSG3413/3573/3978, CRS84Square)
  features.py   load GeoJSON/OSM (by extension), reproject into the grid CRS
  osm.py        read .osm.pbf (pyosmium) + theme: OSM tags → class/subclass
  tiler.py      quadtree descent: clip / simplify / drop / dust / cluster
  aggregate.py  point clustering + attribute accumulation
  encode.py     MVT encode + gzip + tile budgets
  archive.py    PMTiles writer + TMS metadata
  pipeline.py   end-to-end orchestration
  cli.py        command-line entry point
viewer/         reusable OpenLayers viewer (src/ + committed dist/ bundle)
docs/           design notes + the tippecanoe workflow comparison
examples/       the five-projection demo + committed Natural Earth data
serve.py        range-capable static server
tests/          pytest suite
```

## License

MIT
