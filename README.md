# tippykayak

**Non-WebMercator PMTiles, built on [morecantile](https://developmentseed.org/morecantile/) TileMatrixSets.**

[Tippecanoe](https://github.com/felt/tippecanoe) makes gorgeous Web Mercator
vector tiles — it simplifies, drops, and aggregates large geographic datasets
into beautiful, well-balanced tile pyramids. But it only ever emits the
**WebMercatorQuad** tiling scheme. tippykayak fills that gap: it generates
PMTiles on *any* OGC TileMatrixSet — **polar**, **geographic**, or **planetary**
— so you can tile and render data in a CRS where Web Mercator is wrong (the
poles) or meaningless (Mars).

```bash
pip install -e .
tippykayak data.geojson out.pmtiles --tms UPSAntarcticWGS84Quad --maxzoom 8
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
embeds the full TileMatrixSet description in the metadata JSON (the
`crs` / `tile_origin_upper_left_x|y` / `tile_dimension_zoom_0` convention from
the tippecanoe TMS discussions), so a projection-aware client can self-configure.

### 3. You can't get there by wrapping Tippecanoe

felt/tippecanoe has an [open, unimplemented issue (#286)](https://github.com/felt/tippecanoe/issues/286)
to accept a TileMatrixSet; today output is always WebMercatorQuad. The only known
"trick" — pre-warping coordinates to fake EPSG:4326 output — breaks tile
addressing ([#739](https://github.com/mapbox/tippecanoe/issues/739)) and
**cannot** express azimuthal/polar projections, because you can't make Mercator's
fixed math emulate polar stereographic. The projections that make this project
worthwhile are exactly the ones the wrapper trick can't reach.

**Conclusion:** tippykayak is a from-scratch, projection-agnostic tiler in Python
with morecantile as the grid backbone, paired with an OpenLayers front-end.

---

## How it works

```
GeoJSON (lon/lat)
   │  reproject every geometry into the TMS's CRS (pyproj)
   ▼
projected features ── simplify (Douglas-Peucker, scaled per-zoom)
   │               ── drop (size + zoom-stable dot-dropping)
   │               ── clip to each tile (with edge buffer)
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

| Tippecanoe feature | tippykayak v0 |
| --- | --- |
| Simplify | ✅ Douglas-Peucker, tolerance scaled to each zoom's ground resolution |
| Drop (by size) | ✅ features smaller than ~N pixels switch on at the first zoom they're visible |
| Drop (by density) | ✅ deterministic, zoom-stable dot-dropping for points |
| Aggregate / cluster | 🔜 hooks in place (`TileOptions`), not yet implemented |
| Any TileMatrixSet | ✅ — the reason the project exists |

---

## Usage

### CLI

```bash
# List the grids morecantile knows about
tippykayak --list-tms

# Antarctic polar stereographic (EPSG:5042)
tippykayak data.geojson out.pmtiles --tms UPSAntarcticWGS84Quad --minzoom 0 --maxzoom 8

# Plain WGS84 geographic grid
tippykayak data.geojson out.pmtiles --tms WorldCRS84Quad

# Tuning the simplify/drop behaviour
tippykayak data.geojson out.pmtiles \
  --tms UPSArcticWGS84Quad \
  --simplify-pixels 1.0 \
  --min-feature-pixels 1.5 \
  --point-retain 0.6
```

### Python

```python
from tippykayak import Grid, TileOptions, build

grid = Grid.named("UPSAntarcticWGS84Quad")
build("data.geojson", "out.pmtiles", grid,
      TileOptions(layer="ice", min_zoom=0, max_zoom=8))
```

---

## Try the demo

```bash
pip install -e .
python examples/make_sample.py          # writes examples/antarctica.pmtiles
python serve.py                          # range-capable static server, port 8000
# open http://localhost:8000/viewer/index.html
```

![Polar PMTiles rendered in OpenLayers](viewer/preview.png)

The sample is a synthetic Antarctic scene (graticule, ice-shelf ring, research
stations) tiled on the **UPS Antarctic** grid (EPSG:5042) and rendered by
OpenLayers in its native polar CRS — round, centered on the South Pole, with no
Mercator distortion. The viewer self-configures from the TileMatrixSet metadata
embedded in the archive.

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

v0 — the end-to-end path (GeoJSON → polar/geographic PMTiles → OpenLayers) works
and is verified (pytest for the tiler; headless-browser render check for the
viewer). Next on the roadmap: point/feature **aggregation**, FlatGeobuf input,
polygon-area-aware dropping, and a `morecantile.TileMatrixSet.custom()` path for
planetary/IAU CRSs.

## Layout

```
src/tippykayak/
  tms.py        grid math on a morecantile TileMatrixSet (CRS-space)
  features.py   load GeoJSON, reproject into the grid CRS
  tiler.py      simplify / drop / clip → tile pyramid
  encode.py     MVT encode + gzip
  archive.py    PMTiles writer + TMS metadata
  pipeline.py   end-to-end orchestration
  cli.py        command-line entry point
viewer/
  src/main.js       OpenLayers + proj4 + ol-pmtiles viewer source
  dist/             pre-built, CDN-free bundle (committed)
  index.html        loads the bundle
serve.py            range-capable static server (PMTiles needs byte serving)
examples/           sample data generator + committed demo archive
tests/              pytest suite
```

## License

MIT
