# Demo source data

Both committed files come from **Natural Earth** 1:50m physical *land*
(`ne_50m_land`). Natural Earth is in the **public domain** — no permission or
attribution required, though credit is appreciated. Source:
<https://www.naturalearthdata.com/> (vector mirror:
<https://github.com/nvkelso/natural-earth-vector>; CDN:
<https://naciscdn.org/naturalearth/>).

## `arctic_land.geojson`

Natural Earth land clipped to latitude ≥ 40° and lightly simplified (~0.01°).
This is the single shared source for `examples/make_projections.py`, which tiles
it onto four TileMatrixSets (EPSG:3413 / 3573 / 3978 and the geographic
`CRS84Square`). If the file is missing, that script regenerates it from the
Natural Earth CDN (needs the `examples` extra: `pip install -e '.[examples]'`).

## `canada_land.geojson`

Natural Earth land clipped to Canada and lightly simplified; read by
`examples/make_canada.py` to draw coastlines and place the synthetic
infrastructure sites on land.

## Downloaded (not committed)

- **`greenland-latest.osm.pbf`** — a Geofabrik Greenland extract used by
  `examples/make_greenland.py` (real OSM coastlines, ice sheet, waterways and
  places). Fetched on first run; the built `.pmtiles` is the committed artifact.
  Source: <https://download.geofabrik.de/> · © OpenStreetMap contributors, ODbL.
