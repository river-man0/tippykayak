// tippykayak viewer.
//
// Renders non-WebMercator vector PMTiles in their native projected CRS using
// OpenLayers + proj4, self-configuring from the `tippykayak` metadata block each
// archive embeds (PMTiles headers carry no CRS). Handles clustered point layers
// (point_count) with graduated symbols, and can switch between projections.

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

// proj4 ships no EPSG database, so supply the polar definitions we use.
const PROJ_DEFS = {
  'EPSG:3413': '+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs', // NSIDC North
  'EPSG:3573': '+proj=laea +lat_0=90 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',                 // North Pole LAEA
  'EPSG:5042': '+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +k=0.994 +x_0=2000000 +y_0=2000000 +datum=WGS84 +units=m +no_defs', // UPS South
  'EPSG:5041': '+proj=stere +lat_0=90 +lat_ts=90 +lon_0=0 +k=0.994 +x_0=2000000 +y_0=2000000 +datum=WGS84 +units=m +no_defs',   // UPS North
};

const DATASETS = [
  { label: 'Arctic · EPSG:3413 (polar stereographic)', url: '../examples/arctic-3413.pmtiles' },
  { label: 'Arctic · EPSG:3573 (North Pole LAEA)', url: '../examples/arctic-3573.pmtiles' },
];

const status = (m) => (document.getElementById('status').textContent = m);
let map = null;

const LAND_STYLE = new Style({
  zIndex: 0,
  fill: new Fill({ color: 'rgba(38,58,82,0.55)' }),
  stroke: new Stroke({ color: '#4d6f96', width: 0.75 }),
});
const PARALLEL_STYLE = new Style({ zIndex: 1, stroke: new Stroke({ color: '#2f5a44', width: 1 }) });

// Cluster fill ramps from teal (few) to warm (many), by order of magnitude.
function clusterColor(count) {
  if (count >= 100) return 'rgba(255,138,80,0.92)';
  if (count >= 25) return 'rgba(255,196,86,0.9)';
  if (count >= 5) return 'rgba(120,196,160,0.9)';
  return 'rgba(120,180,255,0.9)';
}

function clusterStyle(feature) {
  const kind = feature.get('kind');
  if (kind === 'land') return LAND_STYLE;
  if (kind === 'parallel') return PARALLEL_STYLE;

  const count = feature.get('point_count') || 1;
  if (count > 1) {
    // Area-proportional sizing (radius ∝ √count), clamped.
    const r = Math.min(34, 5 + 2.2 * Math.sqrt(count));
    return new Style({
      zIndex: 2 + Math.min(count, 999),
      image: new CircleStyle({
        radius: r,
        fill: new Fill({ color: clusterColor(count) }),
        stroke: new Stroke({ color: '#0b1622', width: 1.25 }),
      }),
      text: new Text({
        text: String(count), font: 'bold 11px system-ui',
        fill: new Fill({ color: '#0b1622' }),
      }),
    });
  }
  return new Style({
    zIndex: 2,
    image: new CircleStyle({ radius: 3.5, fill: new Fill({ color: '#ffcc44' }), stroke: new Stroke({ color: '#3a2c00', width: 1 }) }),
  });
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

async function show(url) {
  status('loading…');
  const archive = new PMTiles(url);
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

  const tileSize = tk.tile_size;
  const res0 = tk.tile_dimension_zoom_0 / tileSize;
  const resolutions = [];
  for (let z = 0; z <= header.maxZoom; z++) resolutions.push(res0 / Math.pow(2, z));

  const tileGrid = new TileGrid({
    origin: [tk.tile_origin_upper_left_x, tk.tile_origin_upper_left_y],
    resolutions,
    tileSize,
    extent: [minx, miny, maxx, maxy],
  });

  const source = new PMTilesVectorSource({ url, projection, tileGrid });
  const layer = new VectorTileLayer({ source, style: clusterStyle });

  if (map) { map.setTarget(undefined); map = null; }
  map = new Map({
    target: 'map',
    layers: [layer],
    view: new View({ projection, resolutions, constrainResolution: true }),
  });
  map.getView().fit(dataExtent(meta.bounds, epsg), { padding: [40, 40, 40, 40], maxZoom: header.maxZoom, constrainResolution: true });

  status(`grid: ${tk.tilematrixset}\nCRS: ${tk.crs}\nzoom: ${header.minZoom}–${header.maxZoom}\nclustered points · zoom in to split`);
}

function buildSwitcher() {
  const box = document.getElementById('switcher');
  DATASETS.forEach((d, i) => {
    const b = document.createElement('button');
    b.textContent = d.label;
    b.onclick = () => {
      [...box.children].forEach((c) => c.classList.remove('active'));
      b.classList.add('active');
      show(d.url).catch((e) => { status('error: ' + e.message); console.error(e); });
    };
    if (i === 0) b.classList.add('active');
    box.appendChild(b);
  });
}

buildSwitcher();
show(DATASETS[0].url).catch((e) => { status('error: ' + e.message); console.error(e); });
