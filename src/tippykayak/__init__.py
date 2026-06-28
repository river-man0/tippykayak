"""tippykayak — non-WebMercator PMTiles, built on morecantile TileMatrixSets.

Most vector-tile tooling only ever emits the Web Mercator (EPSG:3857) tiling
scheme. tippykayak generates PMTiles on *any* OGC TileMatrixSet — polar or
geographic, including the Arctic grids EPSG:3413 / EPSG:3573 and Canada Atlas
Lambert (EPSG:3978) — for rendering in a projection-aware client such as
OpenLayers.
"""

from .aggregate import Accumulation, Aggregation
from .pipeline import BuildResult, build
from .tiler import TileOptions, build_tiles
from .tms import Grid

__version__ = "0.1.0"

__all__ = [
    "Grid",
    "TileOptions",
    "Aggregation",
    "Accumulation",
    "build",
    "build_tiles",
    "BuildResult",
    "__version__",
]
