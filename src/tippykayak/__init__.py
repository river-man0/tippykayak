"""tippykayak — non-WebMercator PMTiles, built on morecantile TileMatrixSets.

Tippecanoe makes gorgeous Web Mercator vector tiles. tippekayak fills the gap it
leaves: generating PMTiles on *any* OGC TileMatrixSet — polar, geographic, or
planetary — for rendering in a projection-aware client such as OpenLayers.
"""

from .pipeline import BuildResult, build
from .tiler import TileOptions, build_tiles
from .tms import Grid

__version__ = "0.1.0"

__all__ = ["Grid", "TileOptions", "build", "build_tiles", "BuildResult", "__version__"]
