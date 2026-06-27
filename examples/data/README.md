# Demo source data

## `arctic_land.geojson`

Derived from **Natural Earth** 1:50m physical *land* polygons
(`ne_50m_land`), clipped to latitude ≥ 40° and lightly simplified (~0.02°).

Natural Earth is in the **public domain** — no permission or attribution is
required, though credit is appreciated. Source:
<https://www.naturalearthdata.com/> (vector mirror:
<https://github.com/nvkelso/natural-earth-vector>).

Regenerate the clipped file from the upstream `ne_50m_land.geojson` if you want
finer or coarser coastlines; `examples/make_arctic.py` reads this file to draw
coastlines and to place the synthetic settlements on land.
