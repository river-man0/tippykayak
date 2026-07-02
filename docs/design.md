# Design notes: the research that shaped tippykayak

Three findings drove the design. They're worth stating because they rule out
the "obvious" approaches.

## 1. The front-end is OpenLayers, **not** MapLibre

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

## 2. PMTiles is projection-agnostic but **CRS-blind**

The [PMTiles](https://docs.protomaps.com/pmtiles/) format is just a
Hilbert-ordered z/x/y archive. Its header carries zoom/bounds/center but **no CRS
field** — a reader must already know the tiling scheme. tippykayak therefore
embeds the full TileMatrixSet description in the metadata JSON (a
`crs` / `tile_origin_upper_left_x|y` / `tile_dimension_zoom_0` convention) **plus
a `proj4` string and WKT**, so a projection-aware client can configure itself for
any CRS — even one it has never seen — with no hardcoded lookup table.

PMTiles tile IDs walk a Hilbert curve over a square `2^z × 2^z` grid, so a
usable TileMatrixSet must have one tile at zoom 0 and double on both axes each
zoom. That's why the geographic grid tippykayak ships (`CRS84Square`) is a
360°×360° square rather than morecantile's 2×1 `WorldCRS84Quad` — and the same
quadtree property is what the tiler's recursive descent exploits (see
[`tippecanoe.md`](tippecanoe.md)).

## 3. Non-Mercator output has to be generated natively

The common trick for bending a Web-Mercator-only tiler — pre-warping coordinates
to fake EPSG:4326 output — breaks tile addressing and **cannot** express
azimuthal/polar or conic projections: you can't make Mercator's fixed math
emulate polar stereographic or Lambert conformal conic. So the tiling, simplify,
drop, and clustering all have to happen in the target CRS from the start.

**Conclusion:** tippykayak is a from-scratch, projection-agnostic tiler in Python
with morecantile as the grid backbone, paired with an OpenLayers front-end.
Every decision — tile placement, simplification tolerance, feature size
thresholds, cluster cells — is made in the **projected CRS units** of the chosen
grid (metres for projected grids, degrees for geographic ones, straight from
each matrix's `cellSize`), never in Web Mercator. That is the whole point.

## The built-in custom grids

Each extent is a square (a clean power-of-two quad) sized to contain the
projection's useful coverage, so the rendered map isn't clipped at the grid
edge. For the polar azimuthal grids that means a square whose inscribed circle
reaches ~40°N — far enough south to hold the full Arctic landmass.

| id | CRS | projection | extent rationale |
| --- | --- | --- | --- |
| `EPSG3413` | EPSG:3413 | NSIDC polar stereographic (true at 70°N) | at 40°N the projected radius is ~5.77e6 m; ±6e6 m frames the disc with margin |
| `EPSG3573` | EPSG:3573 | North Pole LAEA (Canada, lon₀ −100°) | at 40°N the radius is ~5.40e6 m; ±5.6e6 m holds the landmass |
| `EPSG3978` | EPSG:3978 | NAD83 / Canada Atlas Lambert (conformal conic) | ±4.6e6 m square about the cone apex (the pole), reaching ~49°N |
| `CRS84Square` | OGC:CRS84 | plate carrée (degrees) | 360°×360° square so the quad is PMTiles-addressable; data occupies the northern band and the viewer frames it via `data_extent` |

The EPSG:3978 grid is deliberately centred on the pole to *show a conic's
limitation*: a Lambert conic only spans ~324° of longitude, leaving a ~36°
undefined wedge (bounded by the antimeridian) that the demo renders as a clean
tear rather than a smear.
