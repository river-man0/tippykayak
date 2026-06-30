# Demo source data

## `canada_land.geojson`

Derived from **Natural Earth** 1:50m physical *land* polygons
(`ne_50m_land`), clipped to Canada and lightly simplified.

Natural Earth is in the **public domain** — no permission or attribution is
required, though credit is appreciated. Source:
<https://www.naturalearthdata.com/> (vector mirror:
<https://github.com/nvkelso/natural-earth-vector>). `examples/make_canada.py`
reads this file to draw coastlines and to place the synthetic infrastructure
sites on land.

## Downloaded (not committed)

The real-data demos fetch their sources on first run and git-ignore them; the
built `.pmtiles` archives are the committed artifacts.

- **`simplified-land-polygons-complete-3857/`** — the OpenStreetMap *land
  polygons* product (simplified, EPSG:3857), assembled upstream from OSM
  `natural=coastline` ways. `make_arctic.py` reprojects it to lon/lat, clips it
  to the Arctic and tiles it as the circumpolar coastline.
  Source: <https://osmdata.openstreetmap.de/> · © OpenStreetMap contributors,
  [ODbL](https://opendatacommons.org/licenses/odbl/).
- **`greenland-latest.osm.pbf`** — a Geofabrik Greenland extract, shared by
  `make_greenland.py` and `make_arctic.py` (the latter pulls `natural=glacier`
  for the Greenland ice sheet).
  Source: <https://download.geofabrik.de/> · © OpenStreetMap contributors, ODbL.
