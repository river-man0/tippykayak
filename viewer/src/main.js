// tippykayak polar PMTiles viewer.
//
// Renders a non-WebMercator vector PMTiles archive in its *native* projected CRS
// using OpenLayers + proj4. It self-configures from the `tippykayak` metadata
// block that the tiler embeds in the archive (PMTiles headers carry no CRS).

import Map from 'ol/Map.js';
import View from 'ol/View.js';
import VectorTileLayer from 'ol/layer/VectorTile.js';
import TileGrid from 'ol/tilegrid/TileGrid.js';
import { Style, Stroke, Fill, Circle as CircleStyle, Text } from 'ol/style.js';
import { get as getProjection } from 'ol/proj.js';
import { register } from 'ol/proj/proj4.js';
import proj4 from 'proj4';
import { PMTiles } from 'pmtiles';
import { PMTilesVectorSource } from 'ol-pmtiles';

const PMTILES_URL = '../examples/antarctica.pmtiles';
const status = (m) => (document.getElementById('status').textContent = m);

// proj4 ships no EPSG-code database; supply the polar UPS definitions we expect.
// tippykayak publishes the numeric EPSG code in metadata; we map it to a proj
// string here.
const PROJ_DEFS = {
  'EPSG:5042': '+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +k=0.994 +x_0=2000000 +y_0=2000000 +datum=WGS84 +units=m +no_defs', // UPS South
  'EPSG:5041': '+proj=stere +lat_0=90 +lat_ts=90 +lon_0=0 +k=0.994 +x_0=2000000 +y_0=2000000 +datum=WGS84 +units=m +no_defs',   // UPS North
};

function styleFn(feature) {
  const kind = feature.get('kind');
  if (kind === 'station') {
    return new Style({
      image: new CircleStyle({ radius: 5, fill: new Fill({ color: '#ffcc44' }), stroke: new Stroke({ color: '#3a2c00', width: 1 }) }),
      text: new Text({
        text: feature.get('name') || '', offsetY: -12, font: '12px system-ui',
        fill: new Fill({ color: '#ffe9a8' }), stroke: new Stroke({ color: '#0b1622', width: 3 }),
      }),
    });
  }
  if (kind === 'iceshelf') {
    return new Style({ fill: new Fill({ color: 'rgba(120,180,255,.18)' }), stroke: new Stroke({ color: '#7fb6ff', width: 1.5 }) });
  }
  return new Style({ stroke: new Stroke({ color: kind === 'meridian' ? '#33506e' : '#3f6a4f', width: 1 }) });
}

async function main() {
  const archive = new PMTiles(PMTILES_URL);
  const meta = await archive.getMetadata();
  const header = await archive.getHeader();
  const tk = meta.tippykayak;
  if (!tk) throw new Error('Archive is missing the tippykayak TMS metadata block.');

  const epsg = `EPSG:${tk.epsg}`;
  const def = PROJ_DEFS[epsg];
  if (!def) throw new Error(`No proj4 definition wired up for ${epsg}.`);
  proj4.defs(epsg, def);
  register(proj4);

  const projection = getProjection(epsg);
  const [minx, miny, maxx, maxy] = tk.crs_bounds;
  projection.setExtent([minx, miny, maxx, maxy]);

  // Rebuild the exact grid tippykayak tiled against.
  const tileSize = tk.tile_size;
  const res0 = tk.tile_dimension_zoom_0 / tileSize;
  const maxZoom = header.maxZoom;
  const resolutions = [];
  for (let z = 0; z <= maxZoom; z++) resolutions.push(res0 / Math.pow(2, z));

  const tileGrid = new TileGrid({
    origin: [tk.tile_origin_upper_left_x, tk.tile_origin_upper_left_y],
    resolutions,
    tileSize,
    extent: [minx, miny, maxx, maxy],
  });

  // ol-pmtiles' PMTilesVectorSource handles the VectorTile load lifecycle for us;
  // we hand it our non-WebMercator projection and the matching tile grid.
  const source = new PMTilesVectorSource({
    url: PMTILES_URL,
    projection,
    tileGrid,
  });

  const map = new Map({
    target: 'map',
    layers: [new VectorTileLayer({ source, style: styleFn })],
    view: new View({ projection, resolutions, constrainResolution: true }),
  });

  // Frame the actual data: reproject the geographic bounds (sampling all the way
  // around, since a polar dataset wraps every longitude) into the projection.
  map.getView().fit(dataExtent(meta.bounds, epsg), {
    padding: [40, 40, 40, 40],
    maxZoom,
    constrainResolution: true,
  });

  status(`grid: ${tk.tilematrixset}\nCRS: ${tk.crs}\nzoom: ${header.minZoom}–${header.maxZoom}`);
}

function dataExtent(bounds, epsg) {
  const [minLon, minLat, maxLon, maxLat] = bounds;
  const lats = [minLat, maxLat, (minLat + maxLat) / 2];
  let ext = [Infinity, Infinity, -Infinity, -Infinity];
  for (let lon = -180; lon <= 180; lon += 10) {
    for (const lat of lats) {
      const [x, y] = proj4('EPSG:4326', epsg, [lon, lat]);
      ext = [Math.min(ext[0], x), Math.min(ext[1], y), Math.max(ext[2], x), Math.max(ext[3], y)];
    }
  }
  return ext;
}

main().catch((e) => { status('error: ' + e.message); console.error(e); });
