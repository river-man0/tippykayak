"""Read Geofabrik / OpenStreetMap ``.osm.pbf`` extracts into tippykayak features.

OSM's node/way/relation model — with free-form tags rather than a fixed schema —
is turned into a flat stream of *classified* features by a **theme**: an ordered
list of rules that map OSM tags onto a small, tile-friendly property set
(``class`` / ``subclass`` / ``name``). The agreed output shape is a **single MVT
layer** keyed by ``class``, so the existing single-layer tiler is reused
unchanged.

Everything here stays in geographic coordinates (EPSG:4326). Reprojection into
the target TileMatrixSet's CRS happens downstream in :mod:`tippykayak.features`,
exactly as for GeoJSON input — so OSM data is tiled on *any* OGC grid (polar,
conic, geographic), never assuming Web Mercator.

We read with :class:`osmium.FileProcessor`, assembling way/relation geometry via
``.with_locations()`` / ``.with_areas()`` and exposing GeoJSON geometry through
``osmium.filter.GeoInterfaceFilter``. Each OSM object contributes geometry of one
*kind*:

* **Node**  → point  (e.g. ``place``)
* **Way**   → line   (e.g. ``highway``, ``waterway``, ``natural=coastline``)
* **Area**  → polygon (closed ways + multipolygon relations: water, landuse, …)

A closed area way is delivered *both* as a ``Way`` (a ring line) and as an
``Area`` (the filled polygon). Because line rules are only tested against ``Way``
objects and area rules only against ``Area`` objects, the ring-line form of an
area simply matches nothing and is skipped — no special de-duplication needed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence

import osmium
from osmium.filter import GeoInterfaceFilter
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# Geometry kinds a rule (and an OSM object) can carry.
POINT, LINE, AREA = "point", "line", "area"

# OSM object class name -> the geometry kind it supplies.
_OBJECT_KIND = {"Node": POINT, "Way": LINE, "Area": AREA}

# Tag values that mean "this tag does not actually apply" for boolean-ish keys
# such as ``building`` (``building=no``) — treated as no match.
_NEGATIVE = frozenset({"no", "false", "none"})

# A raw, pre-reprojection feature: (geometry in EPSG:4326, properties, min_zoom).
RawFeature = tuple[BaseGeometry, dict, Optional[int]]


@dataclass(frozen=True)
class Rule:
    """One tag→class mapping rule in a :data:`theme <DEFAULT_THEME>`.

    A rule matches an OSM object when the object's geometry kind equals
    ``geometry`` and its tags contain ``key`` with an acceptable value:

    * ``values=None``  — any value except the negative ones (``no``/``false``);
      this is how open-ended keys like ``building`` or ``landuse`` are matched.
    * ``values={...}`` — the value must be in the set.

    The matched value becomes ``subclass`` (so ``class`` can be coarse — e.g.
    ``landuse=reservoir`` maps to ``class=water, subclass=reservoir``). ``min_zoom``
    is optional: themes can pin a class to a floor zoom, but the built-in theme
    leaves it unset and relies on the tiler's resolution-based size dropping,
    which is grid-agnostic and works on any TileMatrixSet.
    """

    cls: str
    geometry: str
    key: str
    values: Optional[frozenset] = None
    min_zoom: Optional[int] = None

    def match(self, tags: dict) -> Optional[dict]:
        value = tags.get(self.key)
        if value is None:
            return None
        if self.values is None:
            if value in _NEGATIVE:
                return None
        elif value not in self.values:
            return None
        props = {"class": self.cls, "subclass": value}
        name = tags.get("name")
        if name:
            props["name"] = name
        return props


# Road values worth tiling for a general basemap (ordered roughly major→minor).
_HIGHWAY_VALUES = frozenset({
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "service",
    "pedestrian", "track", "path", "footway", "cycleway",
})

# The built-in general-basemap theme. Order matters: the *first* matching rule
# for a feature's geometry kind wins, so specific rules precede catch-alls (e.g.
# ``landuse=reservoir`` → water is listed before the open-ended ``landuse`` rule).
DEFAULT_THEME: tuple[Rule, ...] = (
    # ---- lines (Way) ----
    Rule("coastline", LINE, "natural", frozenset({"coastline"})),
    Rule("waterway", LINE, "waterway",
         frozenset({"river", "stream", "canal", "drain", "ditch"})),
    Rule("road", LINE, "highway", _HIGHWAY_VALUES),
    # ---- points (Node) ----
    Rule("place", POINT, "place",
         frozenset({"city", "town", "village", "hamlet", "suburb",
                    "locality", "island", "islet"})),
    # ---- areas (Area) ----
    Rule("water", AREA, "natural", frozenset({"water", "bay", "strait"})),
    Rule("water", AREA, "waterway", frozenset({"riverbank", "dock"})),
    Rule("water", AREA, "landuse", frozenset({"reservoir", "basin"})),
    Rule("wetland", AREA, "natural", frozenset({"wetland"})),
    Rule("landuse", AREA, "natural",
         frozenset({"wood", "scrub", "heath", "grassland", "glacier",
                    "bare_rock", "scree", "sand", "beach"})),
    Rule("landuse", AREA, "landuse", None),
    Rule("building", AREA, "building", None),
)


def load_theme(path: str | Path) -> tuple[Rule, ...]:
    """Load a theme override from JSON: a list of rule objects.

    Each entry is ``{"class","geometry","key"[,"values"][,"min_zoom"]}`` where
    ``geometry`` is ``point|line|area`` and ``values`` is an optional list (omit
    for open-ended keys). This lets a user retarget the OSM extraction without
    touching code, while the built-in :data:`DEFAULT_THEME` needs no config.
    """
    entries = json.loads(Path(path).read_text())
    rules: list[Rule] = []
    for e in entries:
        values = e.get("values")
        rules.append(
            Rule(
                cls=e.get("class") or e["cls"],
                geometry=e["geometry"],
                key=e["key"],
                values=frozenset(values) if values is not None else None,
                min_zoom=e.get("min_zoom"),
            )
        )
    return tuple(rules)


def _classify(tags: dict, rules: Sequence[Rule]) -> Optional[tuple[dict, Optional[int]]]:
    for rule in rules:
        props = rule.match(tags)
        if props is not None:
            return props, rule.min_zoom
    return None


def _intersects_bbox(geom: BaseGeometry, bbox: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = geom.bounds
    bminx, bminy, bmaxx, bmaxy = bbox
    return not (maxx < bminx or minx > bmaxx or maxy < bminy or miny > bmaxy)


def iter_osm_raw(
    path: str | Path,
    theme: Sequence[Rule] = DEFAULT_THEME,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> Iterator[RawFeature]:
    """Yield ``(geometry, properties, min_zoom)`` for every classified OSM feature.

    Geometry is shapely in EPSG:4326; ``properties`` carries ``class`` /
    ``subclass`` / optional ``name``. ``bbox`` (lon/lat) optionally trims the
    extract so large Geofabrik downloads can be cropped without external tools.
    """
    # Pre-bucket rules by geometry kind so each object only tests relevant rules.
    by_kind: dict[str, list[Rule]] = {POINT: [], LINE: [], AREA: []}
    for rule in theme:
        by_kind[rule.geometry].append(rule)

    processor = (
        osmium.FileProcessor(str(path))
        .with_locations()
        .with_areas()
        .with_filter(GeoInterfaceFilter())
    )

    for obj in processor:
        kind = _OBJECT_KIND.get(type(obj).__name__)
        if kind is None:
            continue
        tags = dict(obj.tags)
        if not tags:
            continue
        classified = _classify(tags, by_kind[kind])
        if classified is None:
            continue
        props, min_zoom = classified

        geom = shape(obj.__geo_interface__["geometry"])
        if geom.is_empty:
            continue
        if bbox is not None and not _intersects_bbox(geom, bbox):
            continue
        yield geom, props, min_zoom
