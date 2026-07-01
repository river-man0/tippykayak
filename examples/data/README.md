# Demo source data

## `arctic_countries.geojson`

**Natural Earth** 1:50m admin-0 countries (`ne_50m_admin_0_countries`), clipped
to latitude ≥ 40° and lightly simplified (~0.01°). Each polygon keeps its country
`name`, which the viewer hashes to a subdued fill colour. This is the single
shared source for `examples/make_projections.py`, which tiles it onto four
TileMatrixSets (EPSG:3413 / 3573 / 3978 and the geographic `CRS84Square`).

Natural Earth is in the **public domain** — no permission or attribution
required, though credit is appreciated. Source:
<https://www.naturalearthdata.com/> (vector mirror:
<https://github.com/nvkelso/natural-earth-vector>; CDN:
<https://naciscdn.org/naturalearth/>). If this file is missing,
`make_projections.py` regenerates it from the CDN (needs the `examples` extra:
`pip install -e '.[examples]'`). The lat/lon graticule, the Arctic Circle, the
red antimeridian and the EPSG:3978 undefined-wedge marker are generated in code,
not committed as data.
