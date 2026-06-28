// tippykayak viewer — a reusable OpenLayers front-end for non-WebMercator PMTiles.
//
// It opens *any* tippykayak archive (URL, ?src= param, or a local file) and
// configures itself entirely from the `tippykayak` metadata block the tiler
// embeds: the projection (proj4 string), the tile grid (origin + zoom-0 span),
// and the layers. No per-dataset code, no hardcoded CRS table required.

import Map from 'ol/Map.js';
import View from 'ol/View.js';
import VectorTileLayer from 'ol/layer/VectorTile.js';
import TileGrid from 'ol/tilegrid/TileGrid.js';
import { Style, Stroke, Fill, Circle as CircleStyle, Text } from 'ol/style.js';
import { get as getProjection } from 'ol/proj.js';
import { register } from 'ol/proj/proj4.js';
import proj4 from 'proj4';
import { PMTiles, FileSource } from 'pmtiles';
import { PMTilesVectorSource } from 'ol-pmtiles';

// Curated demos (served from this repo). Any other archive works via the URL box
// or the file picker.
const DEMOS = [
  { label: 'Arctic · EPSG:3413 (polar stereographic)', url: '../examples/arctic-3413.pmtiles' },
  { label: 'Arctic · EPSG:3573 (North Pole LAEA)', url: '../examples/arctic-3573.pmtiles' },
  { label: 'Antarctic · EPSG:5042 (UPS South)', url: '../examples/antarctica.pmtiles' },
];

// Fallback proj definitions, used only for archives that predate the embedded
// `proj4` metadata. New archives carry their own, so this table is optional.
const PROJ_FALLBACK = {
  'EPSG:3413': '+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
  'EPSG:3573': '+proj=laea +lat_0=90 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
  'EPSG:5042': '+proj=stere +lat_0=-90 +lat_ts=-90 +lon_0=0 +k=0.994 +x_0=2000000 +y_0=2000000 +datum=WGS84 +units=m +no_defs',
  'EPSG:5041': '+proj=stere +lat_0=90 +lat_ts=90 +lon_0=0 +k=0.994 +x_0=2000000 +y_0=2000000 +datum=WGS84 +units=m +no_defs',
};

const setStatus = (html) => (document.getElementById('status').innerHTML = html);
let map = null;
let projCounter = 0; // unique projection code per load, avoids OL proj caching clashes

// ---- styling -------------------------------------------------------------

const LAND_FILL = new Style({ zIndex: 0, fill: new Fill({ color: 'rgba(38,58,82,0.55)' }), stroke: new Stroke({ color: '#4d6f96', width: 0.75 }) });
const POLY_FILL = new Style({ zIndex: 0, fill: new Fill({ color: 'rgba(90,150,210,0.18)' }), stroke: new Stroke({ color: '#5aa9ff', width: 1 }) });
const LINE_STYLE = new Style({ zIndex: 1, stroke: new Stroke({ color: '#5a8f6f', width: 1 }) });
const DOT_STYLE = new Style({ zIndex: 2, image: new CircleStyle({ radius: 3.5, fill: new Fill({ color: '#ffcc44' }), stroke: new Stroke({ color: '#3a2c00', width: 1 }) }) });

function clusterColor(count) {
  if (count >= 100) return 'rgba(255,138,80,0.92)';
  if (count >= 25) return 'rgba(255,196,86,0.9)';
  if (count >= 5) return 'rgba(120,196,160,0.9)';
  return 'rgba(120,180,255,0.9)';
}

// Geometry-type-driven default styling, with cluster bubbles for point_count and
// a couple of `kind` niceties when present.
function styleFor(feature) {
  const type = feature.getType ? feature.getType() : feature.getGeometry().getType();
  if (type === 'Polygon' || type === 'MultiPolygon') {
    return feature.get('kind') === 'land' ? LAND_FILL : POLY_FILL;
  }
  if (type === 'LineString' || type === 'MultiLineString') return LINE_STYLE;

  const count = feature.get('point_count') || 1;
  if (count > 1) {
    const r = Math.min(34, 5 + 2.2 * Math.sqrt(count));
    return new Style({
      zIndex: 2 + Math.min(count, 999),
      image: new CircleStyle({ radius: r, fill: new Fill({ color: clusterColor(count) }), stroke: new Stroke({ color: '#0b1622', width: 1.25 }) }),
      text: new Text({ text: String(count), font: 'bold 11px system-ui', fill: new Fill({ color: '#0b1622' }) }),
    });
  }
  return DOT_STYLE;
}

// ---- loading -------------------------------------------------------------

function projForData(bounds, epsgCode) {
  const [minLon, minLat, maxLon, maxLat] = bounds;
  const lats = [minLat, maxLat, (minLat + maxLat) / 2];
  let ext = [Infinity, Infinity, -Infinity, -Infinity];
  for (let lon = -180; lon <= 180; lon += 10) {
    for (const lat of lats) {
      const [x, y] = proj4('EPSG:4326', epsgCode, [lon, lat]);
      ext = [Math.min(ext[0], x), Math.min(ext[1], y), Math.max(ext[2], x), Math.max(ext[3], y)];
    }
  }
  return ext;
}

async function show(source, label) {
  setStatus('loading…');
  // `pmSource` is what both PMTiles and PMTilesVectorSource consume: a string URL
  // or a pmtiles Source (FileSource for a local file).
  const pmSource = source instanceof File ? new FileSource(source) : source;
  const archive = new PMTiles(pmSource);
  const meta = await archive.getMetadata();
  const header = await archive.getHeader();
  const tk = meta.tippykayak;
  if (!tk) throw new Error('Not a tippykayak archive (missing TMS metadata).');

  // A unique projection code per load so re-registering a tweaked def can't be
  // shadowed by OpenLayers' projection cache.
  const code = `tippykayak:${tk.epsg || tk.tilematrixset}#${projCounter++}`;
  const def = tk.proj4 || PROJ_FALLBACK[`EPSG:${tk.epsg}`];
  if (!def) throw new Error(`Archive has no proj4 metadata and EPSG:${tk.epsg} is not in the fallback table.`);
  proj4.defs(code, def);
  register(proj4);

  const projection = getProjection(code);
  const [minx, miny, maxx, maxy] = tk.crs_bounds;
  projection.setExtent([minx, miny, maxx, maxy]);

  const tileSize = tk.tile_size;
  const res0 = tk.tile_dimension_zoom_0 / tileSize;
  const resolutions = [];
  for (let z = 0; z <= header.maxZoom; z++) resolutions.push(res0 / Math.pow(2, z));

  const tileGrid = new TileGrid({
    origin: [tk.tile_origin_upper_left_x, tk.tile_origin_upper_left_y],
    resolutions, tileSize, extent: [minx, miny, maxx, maxy],
  });

  const vsource = new PMTilesVectorSource({ url: pmSource, projection, tileGrid });
  const layer = new VectorTileLayer({ source: vsource, style: styleFor });

  if (map) { map.setTarget(undefined); map = null; }
  map = new Map({
    target: 'map',
    layers: [layer],
    view: new View({ projection, resolutions, constrainResolution: true }),
  });
  map.getView().fit(projForData(meta.bounds, code), { padding: [40, 40, 40, 40], maxZoom: header.maxZoom, constrainResolution: true });

  const layers = (meta.vector_layers || []).map((l) => l.id).join(', ') || '(none)';
  setStatus(
    `<b>${label || meta.name || tk.tilematrixset}</b><br>` +
    `grid: <code>${tk.tilematrixset}</code><br>` +
    `CRS: <code>${tk.crs}</code> (EPSG:${tk.epsg ?? '—'})<br>` +
    `zoom ${header.minZoom}–${header.maxZoom} · layers: ${layers}`
  );
}

function fail(e) { setStatus(`<span style="color:#ff9a9a">error: ${e.message}</span>`); console.error(e); }

// ---- UI ------------------------------------------------------------------

function wireUI() {
  const demos = document.getElementById('demos');
  DEMOS.forEach((d, i) => {
    const b = document.createElement('button');
    b.textContent = d.label;
    b.onclick = () => { markActive(demos, b); show(d.url, d.label).catch(fail); };
    if (i === 0) b.classList.add('active');
    demos.appendChild(b);
  });

  const urlInput = document.getElementById('url');
  document.getElementById('load').onclick = () => {
    const u = urlInput.value.trim();
    if (u) { markActive(demos, null); show(u, u.split('/').pop()).catch(fail); }
  };
  urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') document.getElementById('load').click(); });

  const file = document.getElementById('file');
  file.addEventListener('change', () => {
    if (file.files[0]) { markActive(demos, null); show(file.files[0], file.files[0].name).catch(fail); }
  });

  // Drag-and-drop a .pmtiles file anywhere on the map.
  const mapEl = document.getElementById('map');
  mapEl.addEventListener('dragover', (e) => { e.preventDefault(); });
  mapEl.addEventListener('drop', (e) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) { markActive(demos, null); show(f, f.name).catch(fail); }
  });
}

function markActive(container, btn) {
  [...container.children].forEach((c) => c.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

wireUI();
const fromUrl = new URLSearchParams(location.search).get('src');
(fromUrl ? show(fromUrl, fromUrl.split('/').pop()) : show(DEMOS[0].url, DEMOS[0].label)).catch(fail);
