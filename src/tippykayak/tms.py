"""TileMatrixSet helpers.

tippykayak builds tile pyramids on *any* OGC TileMatrixSet, not just
WebMercatorQuad. We lean on ``morecantile`` for the grid definitions and add a
thin layer that exposes exactly the per-zoom geometry we need to place projected
coordinates into tiles: the CRS-space origin, the per-zoom resolution, and the
CRS-space span of a single tile.

The single most important departure from "normal" tiling code is that every
quantity here lives in the TMS's *own* projected CRS units (often metres), never
in Web Mercator. That is the whole point of the project.
"""

from __future__ import annotations

from dataclasses import dataclass

import morecantile
from pyproj import CRS

# OGC standardised rendering pixel size, in metres (0.28 mm). morecantile stores
# zoom levels as scale denominators; multiplying by this constant recovers the
# ground resolution in CRS units per pixel, exactly as the OGC TMS spec defines.
STANDARDIZED_PIXEL_SIZE = 0.28e-3


@dataclass(frozen=True)
class ZoomGrid:
    """Everything needed to map a projected coordinate to a tile at one zoom."""

    zoom: int
    matrix_width: int
    matrix_height: int
    tile_size: int
    origin_x: float
    origin_y: float
    resolution: float  # CRS units per pixel

    @property
    def tile_span(self) -> float:
        """Width/height of a single tile, in CRS units."""
        return self.resolution * self.tile_size

    def tile_for(self, x: float, y: float) -> tuple[int, int]:
        """Return the (col, row) of the tile containing projected point (x, y).

        Origin is the upper-left corner, so rows increase as y decreases.
        """
        col = int((x - self.origin_x) // self.tile_span)
        row = int((self.origin_y - y) // self.tile_span)
        return col, row

    def tile_bounds(self, col: int, row: int) -> tuple[float, float, float, float]:
        """CRS-space (minx, miny, maxx, maxy) of tile (col, row)."""
        minx = self.origin_x + col * self.tile_span
        maxy = self.origin_y - row * self.tile_span
        return (minx, maxy - self.tile_span, minx + self.tile_span, maxy)

    def clamp(self, col: int, row: int) -> tuple[int, int]:
        col = max(0, min(col, self.matrix_width - 1))
        row = max(0, min(row, self.matrix_height - 1))
        return col, row


class Grid:
    """A morecantile TileMatrixSet wrapped with tippykayak's tiling helpers."""

    def __init__(self, tms: morecantile.models.TileMatrixSet):
        self.tms = tms
        self.crs: CRS = CRS.from_user_input(tms.crs.srs)
        self._zoom_cache: dict[int, ZoomGrid] = {}

    @classmethod
    def named(cls, identifier: str) -> "Grid":
        """Load a grid by id.

        Resolves tippykayak's built-in custom grids (e.g. ``EPSG3413``) first,
        then falls back to morecantile's registered TileMatrixSets (e.g.
        ``UPSAntarcticWGS84Quad``).
        """
        if identifier in CUSTOM_GRIDS:
            return CUSTOM_GRIDS[identifier]()
        return cls(morecantile.tms.get(identifier))

    @classmethod
    def custom(
        cls,
        crs: CRS | str | int,
        extent: list[float],
        identifier: str,
        *,
        title: str | None = None,
        max_zoom: int = 24,
        tile_size: int = 256,
    ) -> "Grid":
        """Build a quad TileMatrixSet from a CRS and a (square) CRS-space extent.

        This is how tippykayak supports projections morecantile doesn't ship, such
        as the Arctic grids EPSG:3413 and EPSG:3573. A square extent yields a clean
        power-of-two quad (one tile at zoom 0), which is what the viewer's tile
        grid reconstruction assumes.
        """
        crs = crs if isinstance(crs, CRS) else CRS.from_user_input(crs)
        tms = morecantile.TileMatrixSet.custom(
            list(extent),
            crs,
            id=identifier,
            title=title or identifier,
            maxzoom=max_zoom,
            tile_width=tile_size,
            tile_height=tile_size,
        )
        return cls(tms)

    @staticmethod
    def list_named() -> list[str]:
        return sorted(CUSTOM_GRIDS) + morecantile.tms.list()

    @property
    def id(self) -> str:
        return self.tms.id

    @property
    def min_zoom(self) -> int:
        return self.tms.minzoom

    @property
    def max_zoom(self) -> int:
        return self.tms.maxzoom

    def zoom(self, z: int) -> ZoomGrid:
        if z not in self._zoom_cache:
            m = self.tms.matrix(z)
            ox, oy = m.pointOfOrigin
            self._zoom_cache[z] = ZoomGrid(
                zoom=z,
                matrix_width=m.matrixWidth,
                matrix_height=m.matrixHeight,
                tile_size=m.tileWidth,
                origin_x=ox,
                origin_y=oy,
                resolution=m.scaleDenominator * STANDARDIZED_PIXEL_SIZE,
            )
        return self._zoom_cache[z]

    def crs_bounds(self) -> tuple[float, float, float, float]:
        """Full CRS-space bounding box of the grid (minx, miny, maxx, maxy)."""
        bbox = self.tms.xy_bbox
        return (bbox.left, bbox.bottom, bbox.right, bbox.top)

    def describe(self) -> dict:
        """A JSON-serialisable summary of the grid for PMTiles metadata.

        This is the out-of-band CRS information a non-WebMercator client needs:
        PMTiles headers carry no CRS field, so we publish the TMS here following
        the ``crs`` / ``tile_origin`` / ``tile_dimension_zoom_0`` convention.
        """
        z0 = self.zoom(self.min_zoom)
        return {
            "tilematrixset": self.id,
            "crs": self.crs.to_string(),
            "crs_uri": self.tms.crs.srs,
            "epsg": self.crs.to_epsg(),
            "tile_origin_upper_left_x": z0.origin_x,
            "tile_origin_upper_left_y": z0.origin_y,
            "tile_dimension_zoom_0": z0.tile_span,
            "tile_size": z0.tile_size,
            "crs_bounds": list(self.crs_bounds()),
        }


# tippykayak's built-in custom grids for projections morecantile doesn't ship.
#
# EPSG:3413 — NSIDC Sea Ice Polar Stereographic North. The extent (±4194304 m) is
# NASA GIBS's: a power-of-two square that frames the Arctic landmasses.
# EPSG:3573 — North Pole LAEA (Canada). The extent (±4889334.8765 m) places the
# grid edge at exactly 45°N, matching ArcticConnect's 45–90°N coverage.
CUSTOM_GRIDS: dict[str, "callable"] = {
    "EPSG3413": lambda: Grid.custom(
        3413,
        [-4194304.0, -4194304.0, 4194304.0, 4194304.0],
        "EPSG3413",
        title="NSIDC Sea Ice Polar Stereographic North",
        max_zoom=18,
    ),
    "EPSG3573": lambda: Grid.custom(
        3573,
        [-4889334.8765, -4889334.8765, 4889334.8765, 4889334.8765],
        "EPSG3573",
        title="North Pole LAEA (Canada / Beringia)",
        max_zoom=18,
    ),
}
