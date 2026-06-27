"""tippykayak — non-WebMercator PMTiles, built on morecantile TileMatrixSets.

Tippecanoe makes gorgeous Web Mercator vector tiles. tippekayak fills the gap it
leaves: generating PMTiles on *any* OGC TileMatrixSet — polar or geographic,
including the Arctic grids EPSG:3413 and EPSG:3573 — for rendering in a
projection-aware client such as OpenLayers.
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
