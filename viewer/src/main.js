// tippykayak viewer — a reusable OpenLayers front-end for non-Mercator PMTiles.
//
// Opens any tippykayak archive (URL, ?src= param, or a local file) and configures
// itself entirely from the embedded metadata: the projection (proj4 string), the
// tile grid (origin + zoom-0 span), and the layers. No per-dataset code.

import Map from 'ol/Map.js';
import View from 'ol/View.js';
import VectorTileLayer from 'ol/layer/VectorTile.js';
import TileGrid from 'ol/tilegrid/TileGrid.js';
import { Style, Stroke, Fill, Circle as CircleStyle, Icon, Text } from 'ol/style.js';
import { get as getProjection } from 'ol/proj.js';
import { register } from 'ol/proj/proj4.js';
import proj4 from 'proj4';
import { PMTiles, FileSource } from 'pmtiles';
import { PMTilesVectorSource } from 'ol-pmtiles';

const DEMOS = [
  { label: 'Greenland · OSM', url: '../examples/greenland-3413.pmtiles' },
  { label: 'Canada · Lambert', url: '../examples/canada-3978.pmtiles' },
  { label: 'Arctic · Stereographic', url: '../examples/arctic-3413.pmtiles' },
  { label: 'Arctic · LAEA', url: '../examples/arctic-3573.pmtiles' },
];

// Fallback proj defs for archives predating the embedded `proj4` metadata.
const PROJ_FALLBACK = {
  'EPSG:3413': '+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
  'EPSG:3573': '+proj=laea +lat_0=90 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
  'EPSG:3978': '+proj=lcc +lat_0=49 +lon_0=-95 +lat_1=49 +lat_2=77 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs',
};

let map = null;
let projCounter = 0;
const $ = (id) => document.getElementById(id);

// ---- palettes ------------------------------------------------------------

// Category symbology shown for individual sites at the deepest zoom.
const CATEGORIES = {
  airport: { color: '#5fd0ff', label: 'Airport' },
  power: { color: '#ffc861', label: 'Power' },
  radio: { color: '#69e6a3', label: 'Radio' },
  military: { color: '#ff8aa0', label: 'Military' },
};

// Cluster density ramp: cool (few) → warm (many). Each step is an RGB triple.
const CLUSTER_RAMP = [
  { max: 4, rgb: [56, 224, 200] },   // teal
  { max: 12, rgb: [70, 200, 255] },  // cyan
  { max: 39, rgb: [108, 162, 255] }, // blue
  { max: 119, rgb: [155, 120, 255] },// violet
  { max: Infinity, rgb: [255, 121, 198] }, // pink
];

const rgba = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;

// ---- feature styling -----------------------------------------------------

const LAND_STYLE = new Style({ zIndex: 0,
  fill: new Fill({ color: 'rgba(64,96,150,0.16)' }),
  stroke: new Stroke({ color: 'rgba(120,170,235,0.5)', width: 0.8 }) });
const POLY_STYLE = new Style({ zIndex: 0,
  fill: new Fill({ color: 'rgba(90,150,210,0.15)' }),
  stroke: new Stroke({ color: 'rgba(110,170,255,0.7)', width: 1 }) });
const GRATICULE_STYLE = new Style({ zIndex: 1, stroke: new Stroke({ color: 'rgba(125,150,205,0.16)', width: 0.7 }) });
const LINE_STYLE = new Style({ zIndex: 1, stroke: new Stroke({ color: 'rgba(120,200,170,0.8)', width: 1 }) });

// ---- OSM (.osm.pbf) styling, keyed on the `class` property ----------------
// Real OSM archives flatten everything into one layer tagged with `class`; this
// palette gives each class a distinct, restrained look (areas filled, lines
// stroked, places labelled). Legend order doubles as the legend itself.
const CLASS_STYLES = {
  land:     { kind: 'area', fill: 'rgba(64,96,150,0.16)',   stroke: 'rgba(120,170,235,0.5)',  width: 0.8, z: 0, label: 'Land' },
  glacier:  { kind: 'area', fill: 'rgba(225,240,255,0.30)', stroke: 'rgba(210,232,255,0.55)', width: 0.6, z: 1, label: 'Ice / glacier' },
  water:    { kind: 'area', fill: 'rgba(58,120,200,0.40)',  stroke: 'rgba(120,180,255,0.6)',  width: 0.6, z: 2, label: 'Water' },
  wetland:  { kind: 'area', fill: 'rgba(80,160,150,0.26)',  stroke: 'rgba(120,200,180,0.5)',  width: 0.5, z: 2, label: 'Wetland' },
  landuse:  { kind: 'area', fill: 'rgba(96,140,96,0.20)',   stroke: 'rgba(140,190,140,0.35)', width: 0.5, z: 1, label: 'Land use' },
  building: { kind: 'area', fill: 'rgba(190,196,214,0.55)', stroke: 'rgba(220,225,240,0.7)',  width: 0.5, z: 3, label: 'Building' },
  coastline:{ kind: 'line', stroke: 'rgba(150,205,255,0.9)', width: 1.1, z: 4, label: 'Coastline' },
  waterway: { kind: 'line', stroke: 'rgba(95,175,255,0.85)', width: 1.0, z: 4, label: 'Waterway' },
  road:     { kind: 'line', stroke: 'rgba(232,205,150,0.75)', width: 0.9, z: 4, label: 'Road' },
  place:    { kind: 'point', color: '#ffe7a8', z: 6, label: 'Place' },
};

const _osmCache = new Map();
function osmStyle(feature, cls, resolution) {
  const spec = CLASS_STYLES[cls];
  if (!spec) return POLY_STYLE; // unknown class → neutral fallback
  if (spec.kind === 'point') {
    const name = feature.get('name');
    const showLabel = name && resolution <= labelMaxRes;
    const key = 'p:' + (showLabel ? name : '');
    let style = _osmCache.get(key);
    if (!style) {
      style = new Style({
        zIndex: spec.z,
        image: new CircleStyle({ radius: 2.6, fill: new Fill({ color: spec.color }),
          stroke: new Stroke({ color: 'rgba(0,0,0,0.45)', width: 0.6 }) }),
        text: showLabel ? new Text({
          text: name, font: '600 11px Inter, system-ui, sans-serif', offsetY: -9,
          fill: new Fill({ color: '#f3ecd6' }),
          stroke: new Stroke({ color: 'rgba(8,12,24,0.85)', width: 2.5 }),
        }) : undefined,
      });
      _osmCache.set(key, style);
    }
    return style;
  }
  let style = _osmCache.get(cls);
  if (!style) {
    style = spec.kind === 'area'
      ? new Style({ zIndex: spec.z, fill: new Fill({ color: spec.fill }),
          stroke: new Stroke({ color: spec.stroke, width: spec.width }) })
      : new Style({ zIndex: spec.z, stroke: new Stroke({ color: spec.stroke, width: spec.width }) });
    _osmCache.set(cls, style);
  }
  return style;
}

// Resolution at/under which place labels appear; set per-archive in show().
let labelMaxRes = 0;

const _clusterCache = new Map();
function clusterStyle(count) {
  // Small, jewel-like cores: deliberately tiny so the map reads through, with
  // colour (not size) carrying magnitude. Capped hard so dense clusters stay
  // crisp instead of swallowing the basemap.
  const radius = Math.max(2.5, Math.min(7.5, 1.8 + 0.8 * Math.sqrt(count)));
  const tier = CLUSTER_RAMP.findIndex((s) => count <= s.max);
  const key = tier + ':' + radius.toFixed(1);
  let styles = _clusterCache.get(key);
  if (!styles) {
    const rgb = CLUSTER_RAMP[tier].rgb;
    // A faint outer glow behind a bright core finished with a thin bright rim —
    // a premium, gemstone feel rather than a fat blob.
    styles = [
      new Style({ zIndex: 3, image: new CircleStyle({ radius: radius * 2.3, fill: new Fill({ color: rgba(rgb, 0.06) }) }) }),
      new Style({ zIndex: 4, image: new CircleStyle({ radius: radius * 1.45, fill: new Fill({ color: rgba(rgb, 0.16) }) }) }),
      new Style({ zIndex: 5, image: new CircleStyle({
        radius,
        fill: new Fill({ color: rgba(rgb, 0.95) }),
        stroke: new Stroke({ color: 'rgba(255,255,255,0.55)', width: 0.75 }),
      }) }),
    ];
    _clusterCache.set(key, styles);
  }
  return styles;
}

function iconSvg(color) {
  // viewBox-keyed glyphs per category, tinted to `color`.
  return {
    airport: `<path fill="${color}" d="M12 2c.8 0 1.3 1 1.3 2.6v4.2l7.2 4.1v1.9l-7.2-2v3.7l1.9 1.5v1.5L12 19.4l-3.2 1.1v-1.5l1.9-1.5v-3.7l-7.2 2v-1.9l7.2-4.1V4.6C10.7 3 11.2 2 12 2z"/>`,
    power: `<path fill="${color}" d="M13 2 4.5 13.6H10l-1 8.4L19.5 10H13.5z"/>`,
    military: `<path fill="${color}" d="M12 2.2 20 5v6.1c0 5.2-3.4 9-8 10.8-4.6-1.8-8-5.6-8-10.8V5z"/>`,
    radio: `<g fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="9" r="1.6" fill="${color}" stroke="none"/><path d="M12 10.5 9 21M12 10.5l3 10.5M9.9 17h4.2"/><path d="M7.6 5.6a6 6 0 0 0 0 7M16.4 5.6a6 6 0 0 1 0 7"/></g>`,
  };
}

const _singletonCache = new Map();
function singletonStyle(category) {
  let styles = _singletonCache.get(category || '_');
  if (styles) return styles;
  const meta = CATEGORIES[category];
  if (meta) {
    const glyph = iconSvg(meta.color)[category];
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">${glyph}</svg>`;
    const rgb = hexToRgb(meta.color);
    styles = [
      new Style({ zIndex: 6, image: new CircleStyle({ radius: 8, fill: new Fill({ color: rgba(rgb, 0.14) }) }) }),
      new Style({ zIndex: 7, image: new Icon({ src: 'data:image/svg+xml,' + encodeURIComponent(svg), scale: 0.62 }) }),
    ];
  } else {
    styles = [new Style({ zIndex: 6, image: new CircleStyle({ radius: 3, fill: new Fill({ color: 'rgba(150,190,255,0.92)' }) }) })];
  }
  _singletonCache.set(category || '_', styles);
  return styles;
}

function styleFor(feature, resolution) {
  // Real OSM archives carry a `class`; route those to the class palette.
  const cls = feature.get('class');
  if (cls) return osmStyle(feature, cls, resolution);
  const type = feature.getType();
  if (type === 'Polygon' || type === 'MultiPolygon') return feature.get('kind') === 'land' ? LAND_STYLE : POLY_STYLE;
  if (type === 'LineString' || type === 'MultiLineString') {
    const k = feature.get('kind');
    return k === 'parallel' || k === 'meridian' ? GRATICULE_STYLE : LINE_STYLE;
  }
  const count = feature.get('point_count') || 1;
  return count > 1 ? clusterStyle(count) : singletonStyle(feature.get('category'));
}

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// ---- loading -------------------------------------------------------------

function projForData(bounds, code) {
  // Sample a grid over the data's own lon/lat box (not the whole globe) so a
  // regional projection like Lambert fits tightly; for polar data the box spans
  // all longitudes anyway.
  const [minLon, minLat, maxLon, maxLat] = bounds;
  let ext = [Infinity, Infinity, -Infinity, -Infinity];
  const N = 24;
  for (let i = 0; i <= N; i++) {
    const lon = minLon + ((maxLon - minLon) * i) / N;
    for (let j = 0; j <= N; j++) {
      const lat = minLat + ((maxLat - minLat) * j) / N;
      const [x, y] = proj4('EPSG:4326', code, [lon, lat]);
      ext = [Math.min(ext[0], x), Math.min(ext[1], y), Math.max(ext[2], x), Math.max(ext[3], y)];
    }
  }
  return ext;
}

async function show(source, label) {
  setMeta('loading…');
  const pmSource = source instanceof File ? new FileSource(source) : source;
  const archive = new PMTiles(pmSource);
  const meta = await archive.getMetadata();
  const header = await archive.getHeader();
  const tk = meta.tippykayak;
  if (!tk) throw new Error('Not a tippykayak archive (missing TMS metadata).');

  const code = `tippykayak:${tk.epsg || tk.tilematrixset}#${projCounter++}`;
  const def = tk.proj4 || PROJ_FALLBACK[`EPSG:${tk.epsg}`];
  if (!def) throw new Error(`No proj4 for EPSG:${tk.epsg}.`);
  proj4.defs(code, def);
  register(proj4);

  const projection = getProjection(code);
  const [minx, miny, maxx, maxy] = tk.crs_bounds;
  projection.setExtent([minx, miny, maxx, maxy]);

  const tileSize = tk.tile_size;
  const res0 = tk.tile_dimension_zoom_0 / tileSize;
  const resolutions = [];
  for (let z = 0; z <= header.maxZoom; z++) resolutions.push(res0 / Math.pow(2, z));

  // Real OSM archives carry per-feature classes; detect them to drive class
  // styling/legend and place labels (shown only in the deepest few zooms).
  const fields = (meta.vector_layers && meta.vector_layers[0] && meta.vector_layers[0].fields) || {};
  const hasClasses = 'class' in fields;
  labelMaxRes = resolutions[Math.max(0, header.maxZoom - 3)];

  const tileGrid = new TileGrid({ origin: [tk.tile_origin_upper_left_x, tk.tile_origin_upper_left_y], resolutions, tileSize, extent: [minx, miny, maxx, maxy] });
  const vsource = new PMTilesVectorSource({ url: pmSource, projection, tileGrid });
  // declutter keeps OSM place labels from colliding without hand-rolled logic.
  const layer = new VectorTileLayer({ source: vsource, style: styleFor, declutter: hasClasses });

  if (map) { map.setTarget(undefined); map = null; }
  map = new Map({ target: 'map', layers: [layer], controls: [],
    view: new View({ projection, resolutions, constrainResolution: true }) });
  // Prefer the exact projected data extent; fall back to reprojecting bounds.
  // constrainResolution:false here fills the view tightly; interactive zoom still
  // snaps to integer levels (the View's own constraint) for crisp tiles.
  const fitExtent = tk.data_extent || projForData(meta.bounds, code);
  map.getView().fit(fitExtent, { padding: [54, 30, 74, 30], maxZoom: header.maxZoom, constrainResolution: false });

  setMeta(`${tk.title || tk.tilematrixset} · ${tk.crs}`);
  buildLegend(hasClasses);
  enableDrop();
}

function fail(e) { setMeta(`<span class="err">error: ${e.message}</span>`); console.error(e); }
function setMeta(html) { $('meta').innerHTML = html; }

// ---- UI ------------------------------------------------------------------

function buildLegend(osm = false) {
  const items = osm
    ? Object.values(CLASS_STYLES).map((s) => {
        const color = s.kind === 'line' ? s.stroke : (s.kind === 'point' ? s.color : s.fill);
        return { color, label: s.label, glow: s.kind !== 'area' };
      })
    : Object.values(CATEGORIES).map((c) => ({ color: c.color, label: c.label, glow: true }));
  $('legend').innerHTML = items
    .map((c) => `<span class="it"><span class="sw" style="background:${c.color}${c.glow ? `;box-shadow:0 0 8px ${c.color}` : ''}"></span>${c.label}</span>`)
    .join('');
}

function buildChips() {
  const chips = $('chips');
  DEMOS.forEach((d, i) => {
    const b = document.createElement('button');
    b.className = 'chip' + (i === 0 ? ' active' : '');
    b.textContent = d.label;
    b.onclick = () => { setActive(b); closeOpener(); show(d.url, d.label).catch(fail); };
    chips.appendChild(b);
  });
}
function setActive(btn) {
  [...$('chips').children].forEach((c) => c.classList.toggle('active', c === btn));
  if (btn) btn.scrollIntoView({ inline: 'center', block: 'nearest' });
}

function openOpener() { $('opener').hidden = false; $('url').focus(); }
function closeOpener() { $('opener').hidden = true; }

function wireUI() {
  buildLegend();
  buildChips();

  $('openBtn').onclick = (e) => { e.stopPropagation(); $('opener').hidden ? openOpener() : closeOpener(); };
  document.addEventListener('click', (e) => {
    if (!$('opener').hidden && !$('opener').contains(e.target) && e.target !== $('openBtn')) closeOpener();
  });

  const load = () => { const u = $('url').value.trim(); if (u) { setActive(null); closeOpener(); show(u, u).catch(fail); } };
  $('load').onclick = load;
  $('url').addEventListener('keydown', (e) => { if (e.key === 'Enter') load(); });
  $('file').addEventListener('change', () => { const f = $('file').files[0]; if (f) { setActive(null); closeOpener(); show(f, f.name).catch(fail); } });
}

function enableDrop() {
  const m = $('map');
  if (m.dataset.drop) return;
  m.dataset.drop = '1';
  m.addEventListener('dragover', (e) => e.preventDefault());
  m.addEventListener('drop', (e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) { setActive(null); closeOpener(); show(f, f.name).catch(fail); } });
}

wireUI();
const fromUrl = new URLSearchParams(location.search).get('src');
(fromUrl ? show(fromUrl, fromUrl) : show(DEMOS[0].url, DEMOS[0].label)).catch(fail);
