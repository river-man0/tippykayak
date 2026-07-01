# What tippecanoe gets right — and how tippykayak re-derives it for any TileMatrixSet

[Tippecanoe](https://github.com/felt/tippecanoe) is the reference tool for
turning large, rich geographic datasets into a small, single-file static tile
service: point it at GeoJSON, get back an MBTiles/PMTiles archive that any
range-request-capable web host can serve with no tile server at all. It has
earned that position with a handful of design decisions that are worth naming
precisely, because each one is an *algorithmic* idea, not a pile of features —
and almost all of them can be restated for grids tippecanoe itself will never
support. This document is that examination: why tippecanoe is elegant, how its
workflow compares to tippykayak's, and which of its ideas tippykayak now
implements natively on arbitrary morecantile TileMatrixSets.

## The elegances

### 1. The artifact is a file, and the file is the deployment

Tippecanoe's output *is* the tile service. A PMTiles archive is a
Hilbert-ordered `z/x/y` key-value store readable with HTTP Range requests, so
"deploying a map" collapses to "uploading one file". There is no database, no
tile server, no cache to keep warm. Everything else in the tool exists to make
that one file small and honest about the data inside it.

tippykayak shares this end-to-end shape (`build()` → one `.pmtiles`), and adds
the piece PMTiles itself is missing for the non-Mercator world: the archive
carries its own TileMatrixSet description (CRS, proj4, WKT, origin, zoom-0
span) in the metadata, so a projection-aware client can configure itself for a
grid it has never seen.

### 2. The tile is the budget

Tippecanoe's core discipline: **no tile may exceed 500 KB or 200,000
features**, ever, and every other mechanism — dropping, simplification,
coalescing — is a strategy for meeting that budget rather than an end in
itself. This inverts the usual failure mode of naive tilers, which faithfully
emit whatever the data contains and produce 20 MB tiles that no client can
render. The budget makes the *output* predictable regardless of the input.

tippykayak now enforces the same guardrails (`max_tile_bytes`, default 512 000
gzipped — what a PMTiles range request actually transfers — and
`max_tile_features`, default 200 000). An oversized tile sheds its least
important features (smallest projected footprint first, draw order otherwise
preserved), halving the count until it fits. Deterministic, and grid-agnostic
because "footprint" is measured in the grid's own CRS units.

### 3. Representative subsetting, not cartographic generalization

Tippecanoe's stated philosophy is to preserve the *shape and density* of the
data rather than to curate it: at low zoom you should still see that the
points are dense in New York and sparse in Nevada, even though almost all of
them have been dropped. The mechanism is a fixed per-zoom retention rate
(`drop-rate`, default 2.5) applied deterministically per feature — not a
spatial query at render time.

tippykayak's dot-dropping is the same idea with the same guarantee
(`point_retain_per_zoom`, a hash-gated threshold of `retain^(maxzoom−z)`), and
its clustering path goes one step further than dropping: nearby points merge
into representatives that *conserve counts and attribute sums* across zooms.

### 4. Zoom stability by construction

A subtle tippecanoe property: whether a feature survives at zoom `z` is a pure
function of the feature (its hash, its size), never of what happens to be
nearby at encode time. So features never flicker in and out as you zoom — a
point visible at `z` is visible at `z+1`. tippykayak keeps this invariant
everywhere: the dot-drop gate is monotonic in `z`, size-dropping compares one
number (the feature's projected extent) against a per-zoom threshold, and
cluster cells halve exactly each zoom so cluster membership nests.

### 5. The quadtree is the algorithm

The structural heart of tippecanoe's tiler: features are clipped into a tile
once, and each tile is then **split into its four children** recursively. No
geometry is ever re-examined at full length; the cost of producing a tile is
proportional to the detail actually inside it, and a deep pyramid costs little
more than the sum of its leaves. It is the textbook case of "the simplest
algorithm is the strongest": the tiling scheme is a quadtree, so the tiler is
a quadtree traversal.

This was the largest workflow difference from tippykayak, which used to
re-clip every full feature at every zoom — a coastline touching a thousand
z10 tiles was intersected against the *entire* coastline a thousand times,
again per zoom. tippykayak now descends: clip once at the starting zoom, then
recursively clip each tile's already-local geometry into its four children.

Two facts make the descent exact on *any* grid tippykayak accepts:

* PMTiles addressing already requires matrices that double per zoom (a square
  `2^z × 2^z` quad) — precisely the property that makes every tile have four
  children. The pipeline verifies the doubling through every tiled zoom, so
  the same check guards the writer and the tiler.
* The tile edge buffer is fixed in *pixels*, so its CRS-unit size halves each
  zoom. A child's buffered clip window therefore always nests inside its
  parent's, and clipping the child from the parent's clip loses nothing.

Measured on a synthetic arctic dataset (six ~2,000-vertex landmasses, a
4,000-vertex line, 3,000 points) on the EPSG:3413 grid:

| maxzoom | tiles | per-zoom re-clipping | quadtree descent |
| --- | --- | --- | --- |
| 9  | ~19,400  | 12.9 s  | 3.3 s (**3.9×**) |
| 11 | ~103,000 | 192.9 s | 13.4 s (**14.4×**) |

The outputs are tile-for-tile equivalent; the speedup grows with depth because
the old cost scaled with (full geometry size × tiles touched) per zoom while
the descent's scales with the total detail present at each zoom.

### 6. Tiny polygons become dust, not nothing

Tippecanoe's most charming trick. Dropping every sub-pixel polygon erases
whole settlements from low zooms; keeping them all busts the budget. Instead,
tippecanoe *accumulates the area* of dropped polygons and, each time a
pixel's worth accumulates, emits a placeholder square of that size carrying
the triggering feature's attributes. A field of 400 sub-pixel buildings
renders as a handful of building-coloured specks — the town keeps its visual
mass at every zoom, for a few dozen bytes.

tippykayak now does exactly this (`polygon_dust`, on by default). The
accumulator runs per tile in feature order, so it is deterministic, and the
"pixel's worth" quantum is `(min_feature_pixels × resolution(z))²` in CRS
units — metres² on a projected grid, degrees² on a geographic one — so the
trick is projection-correct everywhere.

### 7. Let the data choose the maxzoom (`-zg`)

Choosing a maxzoom by hand is a guess that is nearly always wrong in one
direction or the other. Tippecanoe's `-zg` observes that the right maxzoom is
a property of the *data*: the zoom at which the tile coordinate grid can
finally resolve the spacing between features. It estimates that spacing from
the distances between consecutive features in spatial-index order — a
one-sort stand-in for nearest-neighbour distance — and picks the first zoom
whose quantization step is at least that fine.

tippykayak's `--maxzoom auto` (`guess_max_zoom()`) is the same criterion made
grid-generic: the MVT quantization step at zoom `z` is `tile_span(z) /
extent` *in the grid's own CRS units*, and the target spacing follows
tippecanoe's own recipe (all statistics on log distances, which is what makes
them robust to the orders-of-magnitude skew real data has):

* **points** — consecutive distances along a Z-order sort; the target is the
  *nearby* end of the distribution, `exp(mean − 1.5·stddev)`, halved. The
  closely spaced features are the ones the tile grid must tell apart, so the
  guess listens to them rather than to the average.
* **lines and rings** — segment lengths; the target is an *eighth* of their
  geometric mean, so quantization sits well below the scale of the drawn
  detail instead of exactly at it.

A cumulative tile-count guardrail — no guess may imply a pyramid past ~2M
tiles, estimated from the summed feature envelope areas — caps runaway
answers, exactly as tippecanoe caps its own. Because every distance lives in
CRS units, the guess is equally correct on a metre-based polar grid and a
degree-based geographic one — the place where hardcoding Web Mercator's
40-million-metre world would silently fail.

### 8. Clip first, simplify per tile

Tippecanoe partitions geometry into tiles and then simplifies each tile's
share, rather than simplifying globally per zoom. That sounds backwards until
you notice the invariant it buys: Douglas–Peucker always keeps a segment's
endpoints, and after clipping, the endpoints *are* the crossings of the tile's
buffered edge — so lines cross tile boundaries at their exact original
positions and neighbouring tiles stay seam-free without coordination. It also
means simplification cost is proportional to local detail, and pairs
naturally with the quadtree descent (which hands each tile its full-detail
local geometry to simplify from, avoiding cumulative degradation).

tippykayak's descent adopts this ordering; the tolerance stays
`simplify_pixels × resolution(z)` in CRS units, as before.

### 9. One ordering to rule them all

Tippecanoe sorts features once by a space-filling-curve index and gets three
things from the same sort: bounded-memory streaming (external merge sort),
meaningful "consecutive feature" spacing statistics, and output locality.
PMTiles then requires tiles in Hilbert order — the archive layout *is* a
space-filling curve too. tippykayak uses the curve at both ends (Z-order for
spacing statistics, Hilbert tile ids for the archive) but still holds
features in memory between them; streaming ingestion is the next natural step
and is noted in the roadmap rather than half-built here.

## The comparison, in one table

| tippecanoe concept | its Web-Mercator form | tippykayak's TMS-generic form |
| --- | --- | --- |
| ground resolution at `z` | `40 075 016 m / (2^z · 256)` | `matrix(z).cellSize` — metres *or* degrees, from morecantile |
| tile budget | 500 KB / 200k features | `max_tile_bytes` (gzipped) / `max_tile_features`, shed smallest first |
| drop rate | `2.5^(maxzoom−z)` gate | `point_retain_per_zoom^(maxzoom−z)` hash gate (or conserving clusters) |
| tiny polygons | accumulate area → dust squares | same, quantum `(min_feature_pixels · resolution)²` in CRS units |
| `-zg` | spacing vs. `2^(z+detail)` of the Mercator world | spacing vs. `tile_span(z)/extent` of *this* grid |
| tiler structure | clip into z0, split into 4 children recursively | identical descent; doubling verified per zoom on the actual TMS |
| archive | Hilbert-ordered MBTiles/PMTiles | Hilbert-ordered PMTiles **+ embedded TMS/CRS metadata** |
| grid | Web Mercator only | any square-quad morecantile TMS, incl. custom polar/conic grids |

## What tippykayak deliberately does differently

* **The grid is a parameter, not an assumption.** Every constant tippecanoe
  hardwires to Web Mercator (world span, resolution, buffer, budgets' spatial
  meaning) is derived per zoom from the TileMatrixSet, which is the reason the
  project exists.
* **The archive teaches the client.** PMTiles has no CRS field, so tippykayak
  embeds proj4/WKT/origin/span metadata; tippecanoe never needs to because its
  readers assume Mercator.
* **Clustering conserves, dropping discards.** For points tippykayak prefers
  the aggregation path (counts and sums survive every zoom) and keeps
  dot-dropping as the lightweight fallback.

## Not adopted (yet), on purpose

* **External sort / streaming ingestion** for planet-scale inputs — the
  biggest remaining gap; the current pipeline is deliberately in-memory.
* **Parallel tile encoding** — the descent makes each subtree independent, so
  this is now embarrassingly parallel when it's wanted.
* **Shared-border detection** for polygon topology, **coalescing** of
  same-attribute features, and attribute-driven filter expressions — real
  tippecanoe features, but each is a feature, not a workflow idea; none
  changes the architecture above.
