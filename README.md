# tippykayak

**Non-WebMercator PMTiles, built on [morecantile](https://developmentseed.org/morecantile/) TileMatrixSets.**

Most vector-tile tooling only ever emits the **Web Mercator** (`WebMercatorQuad`)
tiling scheme. tippykayak generates beautiful, well-balanced PMTiles on *any* OGC
TileMatrixSet — **polar** or **geographic** — so you can tile and render data in a
CRS where Web Mercator is wrong: the Arctic (EPSG:3413 / EPSG:3573), Canada Atlas
Lambert (EPSG:3978), and beyond. It simplifies, drops, and clusters large datasets
entirely in the projected space of the chosen grid.

```bash
pip install -e .
tippykayak sites.geojson out.pmtiles --tms EPSG3978 --maxzoom 8
```

---

## Why this project exists (the research that shaped it)

Three findings drove the design. They're worth stating up front because they
rule out the "obvious" approaches.

### 1. The front-end is OpenLayers, **not** MapLibre

- **MapLibre GL JS cannot render non-WebMercator vector tiles.** It always
  renders EPSG:3857. Non-Mercator projections — "load and display vector tiles
  produced in custom coordinate systems," "specify EPSG + tile matrix set" — are
  on the [roadmap](https://maplibre.org/roadmap/maplibre-gl-js/non-mercator-projection/)
  but unimplemented.
- **OpenLayers can, today.** It supports `VectorTile` sources with a custom
  `proj4` projection and a custom `TileGrid`, and has official examples for
  [geographic/OGC vector tiles](https://openlayers.org/en/latest/examples/ogc-vector-tiles-geographic.html)
  and [reprojected vector tiles](https://openlayers.org/en/latest/examples/vector-tiles-reprojected.html).

So tippykayak ships an **OpenLayers** viewer (`viewer/index.html`).

### 2. PMTiles is projection-agnostic but **CRS-blind**

The [PMTiles](https://docs.protomaps.com/pmtiles/) format is just a
Hilbert-ordered z/x/y archive. Its header carries zoom/bounds/center but **no CRS
field** — a reader must already know the tiling scheme. tippykayak therefore
embeds the full TileMatrixSet description in the metadata JSON (a
`crs` / `tile_origin_upper_left_x|y` / `tile_dimension_zoom_0` convention) **plus
a `proj4` string and WKT**, so a projection-aware client can configure itself for
any CRS — even one it has never seen — with no hardcoded lookup table.

### 3. Non-Mercator output has to be generated natively

The common trick for bending a Web-Mercator-only tiler — pre-warping coordinates
to fake EPSG:4326 output — breaks tile addressing and **cannot** express
azimuthal/polar or conic projections: you can't make Mercator's fixed math
emulate polar stereographic or Lambert conformal conic. So the tiling, simplify,
drop, and clustering all have to happen in the target CRS from the start.

**Conclusion:** tippykayak is a from-scratch, projection-agnostic tiler in Python
with morecantile as the grid backbone, paired with an OpenLayers front-end.

---

## How it works

```
GeoJSON (lon/lat)  ──┐
OSM / Geofabrik     │  classify OSM tags → class/subclass via a theme (osm.py)
 .osm.pbf  ─────────┘
   │  reproject every geometry into the TMS's CRS (pyproj), repairing any
   │  reprojection-induced invalidity (common with real coastlines)
   ▼
projected features ── clip once, then split each tile into its 4 children
   │                  (quadtree descent — work ∝ detail actually in the tile)
   │               ── simplify per tile (Douglas-Peucker, scaled per-zoom)
   │               ── drop (size + zoom-stable dot-dropping), sub-pixel
   │                  polygons accumulate into placeholder "dust" squares
   │               ── enforce per-tile budgets (bytes + features)
   ▼
MVT per tile (mapbox-vector-tile, quantized to the tile's CRS bounds)
   │  gzip
   ▼
PMTiles archive  +  embedded TileMatrixSet metadata
   │
   ▼
OpenLayers viewer  (proj4 projection + matching TileGrid)
```

Every decision — tile placement, simplification tolerance, feature size
thresholds — is made in the **projected CRS units** of the chosen grid, never in
Web Mercator. That is the whole point.

The workflow borrows the ideas that make
[tippecanoe](https://github.com/felt/tippecanoe) the reference tool for this
job — quadtree descent, per-tile budgets, tiny-polygon dust, a data-driven
maxzoom — each re-derived in the grid's own CRS units so they hold on polar,
conic and geographic grids alike. The full examination is in
[`docs/tippecanoe.md`](docs/tippecanoe.md).

| Capability | tippykayak |
| --- | --- |
| Simplify | ✅ Douglas-Peucker per tile, tolerance scaled to each zoom's ground resolution |
| Drop (by size) | ✅ features smaller than ~N pixels switch on at the first zoom they're visible |
| Polygon dust | ✅ dropped sub-pixel polygons accumulate into placeholder squares, so dense fields keep their visual mass at low zoom |
| Drop (by density) | ✅ deterministic, zoom-stable dot-dropping for points |
| Aggregate / cluster | ✅ grid clustering with `point_count` + sum/mean/min/max accumulation |
| Tile budgets | ✅ hard per-tile byte + feature caps; oversized tiles shed their smallest features until they fit |
| Auto maxzoom | ✅ `--maxzoom auto` picks the shallowest zoom that resolves the data's spacing (tippecanoe's `-zg`, in CRS units) |
| Quadtree tiler | ✅ clip once, split tiles into their 4 children — cost proportional to local detail, ~14× faster at z11 than re-clipping per zoom |
| Input formats | ✅ GeoJSON **and** OpenStreetMap / Geofabrik `.osm.pbf` |
| Any TileMatrixSet | ✅ — the reason the project exists |

---

## Usage

### CLI

```bash
# List available grids (tippykayak's custom grids first, then morecantile's)
tippykayak --list-tms

# Arctic grids: EPSG:3413 (NSIDC polar stereographic) and EPSG:3573 (North Pole LAEA)
tippykayak data.geojson out.pmtiles --tms EPSG3413 --maxzoom 8
tippykayak data.geojson out.pmtiles --tms EPSG3573 --maxzoom 8

# Canada Atlas Lambert (EPSG:3978, conic) / geographic plate carrée (degrees)
tippykayak data.geojson out.pmtiles --tms EPSG3978 --maxzoom 8
tippykayak data.geojson out.pmtiles --tms CRS84Square

# Let the data choose the maxzoom (tippecanoe's -zg): the shallowest zoom whose
# tile quantization still resolves the spacing of the features
tippykayak data.geojson out.pmtiles --tms EPSG3413 --maxzoom auto

# Tuning the simplify/drop behaviour
tippykayak data.geojson out.pmtiles --tms EPSG3413 \
  --simplify-pixels 1.0 --min-feature-pixels 1.5 --point-retain 0.6

# Per-tile budgets (defaults shown): oversized tiles shed their smallest
# features until they fit. --no-polygon-dust discards sub-pixel polygons
# outright instead of accumulating them into placeholder squares.
tippykayak data.geojson out.pmtiles --tms EPSG3413 \
  --max-tile-bytes 512000 --max-tile-features 200000 --no-polygon-dust
```

### OpenStreetMap / Geofabrik `.osm.pbf` input

Point tippykayak at a [Geofabrik](https://download.geofabrik.de/) extract (or any
`.osm.pbf`) and it tiles it onto **whatever grid you choose** — the OSM data is
read in lon/lat and reprojected into the TMS's CRS exactly like GeoJSON, so it
works on the polar and conic grids too, not just Web Mercator.

```bash
# Auto-detected by the .osm.pbf / .pbf extension — same command, OSM in
tippykayak canada-latest.osm.pbf out.pmtiles --tms EPSG3978 --maxzoom 12

# Trim a big extract to a lon/lat box, and tile the Arctic on a polar grid
tippykayak iceland.osm.pbf ice.pmtiles --tms EPSG3573 --maxzoom 10 \
  --bbox -25,63,-13,67
```

OSM's free-form tags are mapped to a small, tile-friendly schema by a **theme**.
The built-in *general basemap* theme flattens everything into a **single layer**
where each feature carries a `class` (and `subclass`, plus `name` when tagged):

| `class` | geometry | OSM tags |
| --- | --- | --- |
| `coastline` | line | `natural=coastline` |
| `waterway` | line | `waterway=river/stream/canal/drain/ditch` |
| `road` | line | `highway=motorway…track/path` |
| `place` | point | `place=city/town/village/hamlet/locality/island` |
| `water` | area | `natural=water/bay`, `waterway=riverbank/dock`, `landuse=reservoir/basin` |
| `wetland` | area | `natural=wetland` |
| `landuse` | area | `landuse=*`, `natural=wood/scrub/glacier/…` |
| `building` | area | `building=*` |

Style on `class`/`subclass` in the viewer. Override the mapping with a JSON theme
(a list of `{"class","geometry","key","values"?}` rules, first match wins):

```bash
tippykayak region.osm.pbf water.pmtiles --tms EPSG3978 --theme my-theme.json
```

> **Memory:** like the GeoJSON path, the whole input is held in memory. Use
> regional/sub-regional Geofabrik extracts (or `--bbox`) rather than a
> continent-sized planet file.

### Point clustering / aggregation

Instead of dropping points as you zoom out, **cluster** them — nearby points
merge into one representative carrying a count (and optional accumulated
attributes). Counts are conserved across every zoom; clusters split apart as you
zoom in.

```bash
tippykayak settlements.geojson out.pmtiles --tms EPSG3413 \
  --cluster \
  --cluster-distance 44 \
  --cluster-weight population \
  --accumulate sum:population \
  --accumulate max:population
```

Each `--accumulate OP:FIELD[:OUT]` adds an aggregated field (`OP` is
`sum|mean|min|max|count`). Clusters always get a `point_count`, and
`--cluster-weight FIELD` places each cluster at its **centre of mass** weighted by
that field rather than the plain centroid. Cells are inherently zoom-nested (the
cell size halves exactly each zoom), so members stay together as you zoom. These
custom grids are built in (alongside everything morecantile ships):

| id | CRS | projection | extent |
| --- | --- | --- | --- |
| `EPSG3413` | EPSG:3413 | NSIDC polar stereographic (true at 70°N) | ±6 000 000 m (holds the disc to ~40°N) |
| `EPSG3573` | EPSG:3573 | North Pole LAEA (Canada, lon₀ −100°) | ±5 600 000 m (holds the disc to ~40°N) |
| `EPSG3978` | EPSG:3978 | NAD83 / Canada Atlas Lambert (conformal conic) | square, framing Canada |

### Python

```python
from tippykayak import Grid, TileOptions, build, Aggregation, Accumulation

grid = Grid.named("EPSG3573")
build("settlements.geojson", "out.pmtiles", grid,
      TileOptions(
          layer="arctic", min_zoom=0, max_zoom=8,
          aggregation=Aggregation(
              enabled=True, distance_pixels=36,
              accumulate=(Accumulation.parse("sum:population"),),
          ),
      ))
```

---

## Try the demo

```bash
pip install -e '.[examples]'
python examples/make_projections.py      # ONE land dataset → 5 tiling schemes (land-3413/3573/3978/4326/3857)
python serve.py                          # range-capable static server, port 8000
# open http://localhost:8000/viewer/index.html
```

![Circumpolar land re-tiled across four projections, rendered in OpenLayers](viewer/preview.png)

The demo is tippykayak's whole thesis in one screen: **the same data, five tiling
schemes**. `make_projections.py` takes a single source — real **Natural Earth**
countries clipped to latitude ≥ 40° (public domain, each filled a subdued colour),
plus a generated lat/lon graticule, the Arctic Circle, and the red antimeridian —
and tiles it natively onto five TileMatrixSets: **EPSG:3413** (NSIDC polar
stereographic), **EPSG:3573** (North Pole LAEA), **EPSG:3978** (Canada Atlas
Lambert, conic), a geographic plate-carrée grid (**CRS84**), and **EPSG:3857**
(Web Mercator) — the ubiquitous one, included precisely to show how badly it
inflates the Arctic. The viewer's projection switcher flips the identical geography
between them — from a pole-centred disc to a flat lon/lat strip — with nothing but
the embedded per-grid metadata. Every feature is densified before tiling, so the
40° clip reprojects to a smooth curve rather than a straight chord in the polar
views.

The **EPSG:3978** view is centred on the pole to *show a limitation*: a Lambert
conic only spans ~324° of longitude, so it leaves a ~36° **undefined wedge**
(shaded red, emanating from the pole) — the demo splits the data along the conic's
branch cut so the wedge reads as a clean tear rather than smearing over.

Other input formats and features (**OSM/Geofabrik `.osm.pbf`** ingestion, point
**clustering**) are documented below and covered by the test suite; the shipped
demo focuses on the one-dataset/four-projections story.

## The viewer

`viewer/` is a **reusable** OpenLayers front-end for non-WebMercator PMTiles — not
just the demo. It opens *any* tippykayak archive and configures itself from the
metadata the tiler embeds: the **proj4 string** (registered with proj4js), the
tile grid (origin + zoom-0 span), the CRS extent, and the layer list. No
per-dataset code and no hardcoded CRS table — point it at an archive in a
projection it has never seen and it just works.

The UI is a black-and-silver theme with a **projection switcher** as the hero
control: it swaps the shared land dataset between the five tiling schemes live,
while a readout shows the active CRS/TMS and zoom-0 tile span. Open an archive
four ways:

- the **projection switcher** (the five land tiling schemes),
- a remote **URL** (needs CORS + HTTP Range on the host),
- a **local `.pmtiles` file** (picker or drag-and-drop onto the map),
- a **`?src=…`** query parameter, e.g. `viewer/index.html?src=../examples/land-3978.pmtiles`.

Styling adapts to the archive: land renders as a filled silver basemap, OSM
`class` archives get a per-class palette, and point `point_count` clusters render
as soft, density-coloured **glow** blobs with a **category glyph** at the deepest
zoom. The pre-built, CDN-free bundle lives in `viewer/dist/`; rebuild with
`npm run build:viewer`.

> **Why not `python -m http.server`?** PMTiles is read with HTTP **Range**
> requests, which Python's stock server ignores — it returns the whole file and
> PMTiles breaks for any archive past its first read. `serve.py` adds Range
> support. (`npx http-server` or any range-capable server works too.)

### Rebuilding the viewer bundle

The viewer is pre-bundled into `viewer/dist/` (OpenLayers + proj4 + pmtiles +
ol-pmtiles, no CDN). To rebuild after editing `viewer/src/main.js`:

```bash
npm install
npm run build:viewer
```

---

## Status

The end-to-end path (GeoJSON **or OSM/Geofabrik `.osm.pbf`** →
polar/conic/**geographic** PMTiles → OpenLayers) works and is verified (pytest
for the tiler, clustering, OSM ingestion, the geographic grid and the
tippecanoe-derived workflow; headless-browser render checks across all four
projection grids). Simplify, both drop strategies, polygon dust, tile budgets,
auto maxzoom and **clustering aggregation** are implemented on any square-quad
morecantile or custom TileMatrixSet — including degree-based geographic grids,
whose resolution comes from the matrix `cellSize` rather than assuming metres.
The tiler descends the pyramid as a quadtree (see
[`docs/tippecanoe.md`](docs/tippecanoe.md)), so deep zooms cost time proportional
to the detail they contain. Next on the roadmap: streaming/external-sort
ingestion for planet-scale inputs, FlatGeobuf input, and label-collision
handling.

## Layout

```
src/tippykayak/
  tms.py        grid math on morecantile + custom grids (EPSG3413/3573/3978, CRS84Square)
  features.py   load GeoJSON/OSM (by extension), reproject into the grid CRS
  osm.py        read .osm.pbf (pyosmium) + theme: OSM tags → class/subclass
  tiler.py      quadtree descent: clip / simplify / drop / dust / cluster → tile pyramid
  aggregate.py  point clustering + attribute accumulation
  encode.py     MVT encode + gzip
  archive.py    PMTiles writer + TMS metadata
  pipeline.py   end-to-end orchestration
  cli.py        command-line entry point
viewer/
  src/main.js       reusable OpenLayers viewer (URL / file / ?src=, self-configuring)
  dist/             pre-built, CDN-free bundle (committed)
  index.html        loads the bundle
serve.py            range-capable static server (PMTiles needs byte serving)
docs/
  tippecanoe.md     why tippecanoe is elegant, and how its workflow maps onto any TMS
examples/
  make_projections.py one Natural Earth clip → 5 tiling schemes (3413/3573/3978/CRS84/3857)
  data/               committed Natural Earth land + boundary clips (public domain)
tests/              pytest suite
```

## License

MIT
