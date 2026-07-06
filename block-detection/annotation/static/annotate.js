// Canvas annotation : dessine des bbox sur l'image, sauve en DB.
'use strict';

const stage    = document.getElementById('stage');
const canvas   = document.getElementById('canvas');
const ctx      = canvas.getContext('2d');
const statusEl = document.getElementById('status');
const coordEl  = document.getElementById('coord');
const labelsEl = document.querySelector('.labels-list');
const annoListEl = document.getElementById('anno-list');

const imageId = window.IMAGE_ID;
const IMG_W   = window.IMAGE_W;
const IMG_H   = window.IMAGE_H;

const bgImg = document.getElementById('bgimg');  // image de fond (élément <img>)
let zoom = 1;
let panX = 0, panY = 0;
let isPanning = false; let panStart = null;

let labels = [];
let labelById = {};
let currentLabel = null;

let annotations = [];     // {id, label_id, label_name, label_color, x0,y0,x1,y1}
let selectedId  = null;
let tmpSeq = 0;           // ids temporaires pour l'affichage optimiste des boîtes
let highlightOverlaps = true;  // surligne les zones de chevauchement (touche 'h')

let isDrawing = false;
let drawStart = null;     // {x, y} in image coords
let drawCur   = null;
let resizeMode = null;    // 'nw','ne','sw','se' or null
let dragOffset = null;    // pour drag des boites

const HANDLE = 8;         // px sur ecran

// Label par défaut affecté aux blocs détectés (moins de reclassements à faire)
const DETECT_LABEL_NAME = 'bloc de texte';
let detectLabel = null;

// Snapshot avant un resize/drag (pour l'undo)
let dragBefore = null;

// ── Undo (Ctrl+Z) : pile de fonctions qui défont la dernière action ──
const undoStack = [];
function pushUndo(fn) { undoStack.push(fn); if (undoStack.length > 200) undoStack.shift(); }
async function undo() {
  const fn = undoStack.pop();
  if (!fn) { statusEl.textContent = 'Rien à annuler'; return; }
  await fn();
}

// ── Auto-scroll quand on dessine/déplace près d'un bord du stage ──
const EDGE_ZONE = 45;     // distance au bord (px écran) qui déclenche le défilement
const PAN_STEP  = 16;     // vitesse (px par frame)
let autoPan = { dx: 0, dy: 0 };
let autoPanRAF = null;
let lastClient = { x: 0, y: 0 };
function isDragging() { return isDrawing || !!resizeMode || !!dragOffset; }

// ────────────────────────────────────────────────────────────
// Verbose / perf — pour comprendre les à-coups
//   Activer : ?verbose=1 dans l'URL, ou touche 'v', ou localStorage.
//   Tout est loggé dans la console du navigateur (F12) avec le préfixe [perf].
// ────────────────────────────────────────────────────────────
let VERBOSE = new URLSearchParams(location.search).has('verbose')
           || localStorage.getItem('annot_verbose') === '1';
const FRAME_BUDGET = 16;   // ms : au-delà, une frame « rate » le 60 fps

// Renvoie aussi les logs verbose vers le serveur (visibles dans sa sortie), pour
// pouvoir observer le comportement à distance. Rate-limité pour ne pas inonder.
let _beaconBudget = 0, _beaconWin = 0;
function clientBeacon(level, args) {
  if (!VERBOSE || !navigator.sendBeacon) return;
  const now = Date.now();
  if (now - _beaconWin > 1000) { _beaconWin = now; _beaconBudget = 12; }
  if (_beaconBudget-- <= 0) return;
  try {
    const msg = level + ' ' + args.map(a =>
      typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ');
    navigator.sendBeacon('/api/clientlog', msg);
  } catch (e) { /* sans gravité */ }
}
function vlog(...args) { if (VERBOSE) { console.log('[perf]', ...args); clientBeacon('LOG ', args); } }
function vwarn(...args) { if (VERBOSE) { console.warn('[perf]', ...args); clientBeacon('WARN', args); } }

// Beacon TOUJOURS actif (même sans mode verbose) : ne remonte que les blocages
// sévères, fortement rate-limité. Permet de détecter les lags réels sans que
// l'utilisateur ait à activer quoi que ce soit.
let _sevWin = 0, _sevBudget = 0;
function severeBeacon(msg) {
  if (!navigator.sendBeacon) return;
  const now = Date.now();
  if (now - _sevWin > 2000) { _sevWin = now; _sevBudget = 4; }
  if (_sevBudget-- <= 0) return;
  try { navigator.sendBeacon('/api/clientlog', 'SEVERE ' + msg); } catch (e) { /* */ }
}

// Chronomètre une section synchrone ; ne lowarn que si elle dépasse `budget` ms.
function vspan(label, budget, fn) {
  if (!VERBOSE) return fn();
  const t0 = performance.now();
  const r = fn();
  const dt = performance.now() - t0;
  if (dt >= budget) vwarn(`${label}: ${dt.toFixed(1)} ms`);
  else vlog(`${label}: ${dt.toFixed(1)} ms`);
  return r;
}

// fetch instrumenté : loggue méthode, URL et durée (réseau = cause fréquente d'à-coups).
async function vfetch(url, opts) {
  if (!VERBOSE) return fetch(url, opts);
  const m = (opts && opts.method) || 'GET';
  const t0 = performance.now();
  try {
    return await fetch(url, opts);
  } finally {
    const dt = performance.now() - t0;
    (dt >= 200 ? vwarn : vlog)(`fetch ${m} ${url} — ${dt.toFixed(0)} ms`);
  }
}

// Mesure du FPS pendant un drag : on compte les paints entre mousedown et mouseup.
let dragFrames = 0, dragT0 = 0, dragWorstPaint = 0;
function dragPerfStart() {
  if (!VERBOSE) return;
  dragFrames = 0; dragWorstPaint = 0; dragT0 = performance.now();
}
function dragPerfEnd(kind) {
  if (!VERBOSE || !dragT0) return;
  const dt = performance.now() - dragT0;
  const fps = dt > 0 ? (dragFrames / dt * 1000) : 0;
  vlog(`drag(${kind}) terminé : ${dragFrames} frames en ${dt.toFixed(0)} ms ` +
       `≈ ${fps.toFixed(0)} fps, paint le + lent ${dragWorstPaint.toFixed(1)} ms, ` +
       `${annotations.length} boîtes`);
  dragT0 = 0;
}

// Moniteur de frames : mesure les intervalles entre frames pendant le zoom/pan.
// Le zoom/pan ne repeint PAS le canvas (juste un transform CSS) ; un à-coup là
// vient du compositeur qui re-rastérise un canvas + un background-image énormes.
// Une frame > 33 ms = stutter visible.
let fmRAF = null, fmLast = 0, fmTag = '', fmDropped = 0, fmCount = 0, fmStopTimer = null;
function startFrameMonitor(tag) {
  fmTag = tag;
  if (!VERBOSE || fmRAF) return;
  fmLast = performance.now(); fmDropped = 0; fmCount = 0;
  const tick = now => {
    const d = now - fmLast; fmLast = now; fmCount++;
    if (d > 33) { fmDropped++; vwarn(`frame ${d.toFixed(0)} ms pendant ${fmTag}`); }
    fmRAF = requestAnimationFrame(tick);
  };
  fmRAF = requestAnimationFrame(tick);
}
function stopFrameMonitor() {
  if (!fmRAF) return;
  cancelAnimationFrame(fmRAF); fmRAF = null;
  vlog(`${fmTag} : ${fmCount} frames, ${fmDropped} lentes (>33 ms)`);
}
// Pour le zoom (molette, événements discrets) : démarre le moniteur et l'arrête
// après une courte inactivité.
function zoomMonitorPing() {
  if (!VERBOSE) return;
  startFrameMonitor('zoom');
  clearTimeout(fmStopTimer);
  fmStopTimer = setTimeout(stopFrameMonitor, 400);
}

// Observateur natif : capte TOUT blocage du thread principal > 50 ms, quelle que
// soit sa source (GC, layout, script tiers…). C'est le filet pour « ça lag parfois ».
if (window.PerformanceObserver) {
  try {
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) {
        if (VERBOSE) vwarn(`long task ${e.duration.toFixed(0)} ms (blocage du thread)`);
        // Toujours remonter les blocages sévères, même hors mode verbose.
        if (e.duration >= 150)
          severeBeacon(`long task ${e.duration.toFixed(0)} ms (blocage thread) sur ${location.pathname}`);
      }
    }).observe({ entryTypes: ['longtask'] });
  } catch (e) { /* longtask non supporté */ }
}

// ────────────────────────────────────────────────────────────
// Init
// ────────────────────────────────────────────────────────────
async function init() {
  // Charger l'image : c'est un élément <img> à part, transformé (pan/zoom) par
  // le navigateur. Le canvas overlay reste à la taille du viewport et ne dessine
  // que les boîtes -> plus de canvas géant (68 Mpx) à rastériser à chaque frame.
  bgImg.onload = () => {
    bgImg.style.width  = IMG_W + 'px';
    bgImg.style.height = IMG_H + 'px';
    resizeCanvas();
    fitToView();
    render();
    statusEl.textContent = `Image ${IMG_W}x${IMG_H}, zoom ${(zoom*100).toFixed(0)}%`;
    logEnvInfo();
  };
  bgImg.src = `/api/image/${imageId}/file`;
  if (VERBOSE) vlog('mode verbose actif (touche v pour basculer)');

  // Charger labels
  const ls = await fetch('/api/labels').then(r => r.json());
  labels = ls;
  for (const l of ls) labelById[l.id] = l;
  detectLabel = labels.find(l => l.name === DETECT_LABEL_NAME) || labels[0];

  // Auto-select 1er label
  if (labels.length) selectLabel(labels[0].id);

  // Charger annotations
  await reloadAnnotations();

  // Suggestions du modèle (boîtes pré-calculées)
  await refreshSuggestButton();

  // Arrivée depuis l'onglet Corrections : ?focus=x0,y0,x1,y1&sel=ID
  // -> cadrer la vue sur la zone et sélectionner la boîte à ajuster.
  const params = new URLSearchParams(location.search);
  const focus = params.get('focus');
  if (focus) {
    const [fx0, fy0, fx1, fy1] = focus.split(',').map(Number);
    const sel = parseInt(params.get('sel'));
    if (sel && annotations.some(a => a.id === sel)) { selectedId = sel; refreshAnnoList(); }
    const go = () => zoomToImageRegion(fx0, fy0, fx1, fy1);
    if (bgImg.complete && bgImg.naturalWidth) go();
    else bgImg.addEventListener('load', go, { once: true });   // après le fitToView du onload
  }
}

// ────────────────────────────────────────────────────────────
// Suggestions du détecteur
// ────────────────────────────────────────────────────────────
const suggestBtn  = document.getElementById('btn-suggest');
const suggestHint = document.getElementById('suggest-hint');
const detectBtn   = document.getElementById('btn-detect');

async function applySuggestions() {
  const label = detectLabel || currentLabel;
  const r = await fetch(`/api/image/${imageId}/apply-suggestions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label_id: label.id }),
  }).then(r => r.json());
  const ids = r.ids || [];
  if (ids.length) pushUndo(async () => {
    for (const id of ids) await fetch(`/api/annotations/${id}`, { method: 'DELETE' });
    selectedId = null;
    await reloadAnnotations();
    await refreshSuggestButton();
  });
  await reloadAnnotations();
  await refreshSuggestButton();
  return r.created;
}

detectBtn.addEventListener('click', async () => {
  const oldText = detectBtn.textContent;
  detectBtn.disabled = true;
  detectBtn.textContent = '⏳ Détection en cours… (~10s)';
  statusEl.textContent = 'Détection des blocs par le modèle…';
  try {
    const r = await fetch(`/api/image/${imageId}/detect`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const data = await r.json();
    if (!r.ok) { statusEl.textContent = 'Erreur détection : ' + (data.error || r.status); return; }
    if (!data.count) { statusEl.textContent = 'Aucun bloc détecté.'; return; }
    const created = await applySuggestions();
    statusEl.textContent = `${created} blocs détectés (label "${(detectLabel||currentLabel).name}") — reclasse-les`;
  } catch (e) {
    statusEl.textContent = 'Erreur détection : ' + e;
  } finally {
    detectBtn.disabled = false;
    detectBtn.textContent = oldText;
  }
});

async function refreshSuggestButton() {
  // N'affiche le bouton « Charger » que s'il existe des suggestions pré-calculées
  // (en lot). Le hint reste toujours visible : il documente aussi « Détecter ».
  const sugg = await fetch(`/api/image/${imageId}/suggestions`).then(r => r.json());
  if (sugg.length) {
    suggestBtn.textContent = `✨ Charger ${sugg.length} suggestions`;
    suggestBtn.style.display = '';
  } else {
    suggestBtn.style.display = 'none';
  }
}

suggestBtn.addEventListener('click', async () => {
  suggestBtn.disabled = true;
  const created = await applySuggestions();
  statusEl.textContent = `${created} boîtes ajoutées (label "${(detectLabel||currentLabel).name}") — reclasse-les`;
  suggestBtn.disabled = false;
});

// ────────────────────────────────────────────────────────────
// Fusion des blocs qui se chevauchent (bouton bascule)
// ────────────────────────────────────────────────────────────
const mergeBtn = document.getElementById('btn-merge');
const mergeThreshEl = document.getElementById('merge-thresh');
const mergeThreshValEl = document.getElementById('merge-thresh-val');
let merged = false;
let mergeInfo = null;        // [{keeperId, orig, deleted:[...]}] pour annuler

// Seuil d'alignement réglé depuis l'UI (curseur unique). Plus haut = plus strict
// (il faut des boîtes mieux alignées pour fusionner), plus bas = plus permissif.
function mergeThreshold() { return parseFloat(mergeThreshEl.value); }
mergeThreshEl.addEventListener('input', () => {
  mergeThreshValEl.textContent = mergeThreshold().toFixed(2);
});

// ── Réglages de fusion (défauts codés en dur, ajustables ici) ──
const MERGE_ORIENT_TOL = 1.25;  // ratio w/h dans [1/1.25, 1.25] => orientation « ambiguë »
const MERGE_STRONG_OVL = 0.85;  // recouvrement (inter/min) qui autorise la fusion MALGRÉ
                                // des orientations opposées (échappatoire quasi-doublon)
const MERGE_MIN_OVL_PX = 25;    // aire d'intersection minimale (px²) = vrai chevauchement

// Orientation d'une boîte d'après son ratio largeur/hauteur.
function boxOrient(b) {
  const w = b.x1 - b.x0, h = b.y1 - b.y0;
  if (w <= 0 || h <= 0) return 'A';
  const r = w / h;
  if (r > MERGE_ORIENT_TOL) return 'L';        // paysage (large : titre, bandeau)
  if (r < 1 / MERGE_ORIENT_TOL) return 'P';    // portrait (haut : colonne de texte)
  return 'A';                                  // ambigu (~carré)
}

// Décide si deux boîtes doivent fusionner, en tenant compte de :
//  - l'aire de chevauchement (filtre les contacts insignifiants),
//  - l'orientation (on ne fusionne pas un titre horizontal et une colonne
//    verticale qui se croisent — sauf recouvrement très fort),
//  - l'alignement sur l'axe pertinent : deux portraits doivent partager la même
//    plage horizontale (même colonne) ; deux paysages la même plage verticale.
function shouldMerge(a, b, alignMin) {
  const ix = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
  const iy = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
  if (ix <= 0 || iy <= 0) return false;            // pas de chevauchement réel
  if (ix * iy < MERGE_MIN_OVL_PX) return false;    // chevauchement négligeable

  const oa = boxOrient(a), ob = boxOrient(b);
  // Orientations opposées (paysage × portrait) : seulement si l'une est presque
  // entièrement dans l'autre (quasi-doublon), jamais sur un simple croisement.
  if ((oa === 'L' && ob === 'P') || (oa === 'P' && ob === 'L')) {
    return boxOverlapMin(a, b) >= MERGE_STRONG_OVL;
  }
  // Alignement = chevauchement sur l'axe / plus grande étendue sur cet axe.
  const alignX = ix / Math.max(a.x1 - a.x0, b.x1 - b.x0);
  const alignY = iy / Math.max(a.y1 - a.y0, b.y1 - b.y0);
  const hasPortrait  = oa === 'P' || ob === 'P';
  const hasLandscape = oa === 'L' || ob === 'L';
  if (hasPortrait)  return alignX >= alignMin;     // même colonne (alignés en x)
  if (hasLandscape) return alignY >= alignMin;     // même ligne   (alignés en y)
  return Math.max(alignX, alignY) >= alignMin;     // deux ambigus : l'un ou l'autre
}

// Regroupe les boîtes à fusionner (transitif : A-B-C -> 1 groupe).
function computeMergeGroups(boxes, alignMin) {
  const n = boxes.length;
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = x => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++)
      if (shouldMerge(boxes[i], boxes[j], alignMin)) parent[find(i)] = find(j);
  const groups = {};
  for (let i = 0; i < n; i++) (groups[find(i)] ||= []).push(boxes[i]);
  return Object.values(groups);
}

async function mergeBlocks() {
  const groups = computeMergeGroups(annotations, mergeThreshold()).filter(g => g.length > 1);
  if (!groups.length) {
    statusEl.textContent = 'Rien à fusionner : aucun fort chevauchement.';
    return;
  }
  mergeInfo = [];
  let absorbed = 0;
  for (const g of groups) {
    const keeper = g[0];
    const orig = { x0: keeper.x0, y0: keeper.y0, x1: keeper.x1, y1: keeper.y1, label_id: keeper.label_id };
    const x0 = Math.round(Math.min(...g.map(a => a.x0)));
    const y0 = Math.round(Math.min(...g.map(a => a.y0)));
    const x1 = Math.round(Math.max(...g.map(a => a.x1)));
    const y1 = Math.round(Math.max(...g.map(a => a.y1)));
    // Le keeper devient la boîte englobante ; les autres sont supprimées.
    await fetch(`/api/annotations/${keeper.id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x0, y0, x1, y1, label_id: keeper.label_id }),
    });
    const deleted = [];
    for (const a of g.slice(1)) {
      deleted.push({ label_id: a.label_id, x0: a.x0, y0: a.y0, x1: a.x1, y1: a.y1 });
      await fetch(`/api/annotations/${a.id}`, { method: 'DELETE' });
      absorbed++;
    }
    mergeInfo.push({ keeperId: keeper.id, orig, deleted });
  }
  merged = true;
  selectedId = null;
  await reloadAnnotations();
  statusEl.textContent = `${groups.length} fusion(s), ${absorbed} boîtes absorbées — reclique pour annuler.`;
}

async function unmergeBlocks() {
  if (mergeInfo) {
    for (const m of mergeInfo) {
      // Le keeper retrouve sa taille d'origine…
      await fetch(`/api/annotations/${m.keeperId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          x0: Math.round(m.orig.x0), y0: Math.round(m.orig.y0),
          x1: Math.round(m.orig.x1), y1: Math.round(m.orig.y1),
          label_id: m.orig.label_id,
        }),
      });
      // …et on recrée les boîtes absorbées.
      for (const d of m.deleted) {
        await fetch('/api/annotations', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            image_id: imageId, label_id: d.label_id,
            x0: Math.round(d.x0), y0: Math.round(d.y0),
            x1: Math.round(d.x1), y1: Math.round(d.y1),
          }),
        });
      }
    }
  }
  mergeInfo = null;
  merged = false;
  selectedId = null;
  await reloadAnnotations();
  statusEl.textContent = 'Fusion annulée.';
}

function updateMergeBtn() {
  mergeBtn.textContent = merged ? '↩︎ Annuler la fusion' : '🔗 Fusionner les blocs chevauchants';
  mergeBtn.classList.toggle('warn', merged);
  mergeBtn.classList.toggle('ok', !merged);
}

mergeBtn.addEventListener('click', async () => {
  mergeBtn.disabled = true;
  try {
    if (!merged) await mergeBlocks(); else await unmergeBlocks();
  } catch (e) {
    statusEl.textContent = 'Erreur fusion : ' + e;
  } finally {
    mergeBtn.disabled = false;
    updateMergeBtn();
  }
});
updateMergeBtn();

async function reloadAnnotations() {
  annotations = await vfetch(`/api/image/${imageId}/annotations`).then(r => r.json());
  vspan('refreshAnnoList (DOM)', 8, refreshAnnoList);
  render();
}

// ────────────────────────────────────────────────────────────
// Coordonnees : ecran <-> image
// ────────────────────────────────────────────────────────────
function screenToImage(sx, sy) {
  // Le canvas n'est plus transformé : on convertit via pan/zoom à la main.
  const rect = stage.getBoundingClientRect();
  return { x: (sx - rect.left - panX) / zoom, y: (sy - rect.top - panY) / zoom };
}

// Dimensionne le canvas overlay à la taille du stage (× devicePixelRatio pour
// rester net). À appeler au chargement et au redimensionnement de la fenêtre.
function resizeCanvas() {
  const rect = stage.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width  = rect.width + 'px';
  canvas.style.height = rect.height + 'px';
  canvas.width  = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
}
window.addEventListener('resize', () => { resizeCanvas(); render(); });

function fitToView() {
  const rect = stage.getBoundingClientRect();
  zoom = Math.min(rect.width / IMG_W, rect.height / IMG_H) * 0.95;
  panX = (rect.width  - IMG_W * zoom) / 2;
  panY = (rect.height - IMG_H * zoom) / 2;
  applyTransform();
}

// Cadre la vue sur une région image [x0,y0,x1,y1] (centrée, avec une marge).
function zoomToImageRegion(x0, y0, x1, y1, margin = 0.18) {
  const rect = stage.getBoundingClientRect();
  const rw = Math.max(1, x1 - x0), rh = Math.max(1, y1 - y0);
  zoom = Math.min(rect.width / rw, rect.height / rh) * (1 - margin);
  zoom = Math.max(0.05, Math.min(zoom, 8));
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  panX = rect.width / 2 - cx * zoom;
  panY = rect.height / 2 - cy * zoom;
  applyTransform();
}

// pan/zoom : on transforme l'élément <img> (le navigateur compose), et on
// repeint l'overlay des boîtes (rAF-throttlé) pour les replacer.
function applyTransform() {
  bgImg.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  render();
}

// ────────────────────────────────────────────────────────────
// Rendu
// ────────────────────────────────────────────────────────────
// render() ne fait qu'ordonnancer un repaint : plusieurs appels dans la même
// frame (rafale de mousemove) se coalescent en un seul paint via rAF.
let renderPending = false;
function render() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(paint);
}

function paint() {
  renderPending = false;
  const t0 = VERBOSE ? performance.now() : 0;
  // Canvas overlay (taille viewport) : on efface en pixels device, puis on
  // applique pan/zoom au contexte (+ devicePixelRatio) pour pouvoir dessiner les
  // boîtes en coordonnées IMAGE, comme avant. L'image de fond est l'<img>.
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.translate(panX, panY);
  ctx.scale(zoom, zoom);

  // Annotations existantes
  for (const a of annotations) {
    const isSel = a.id === selectedId;
    drawRect(a.x0, a.y0, a.x1, a.y1, a.label_color, isSel);
    // Label en haut a gauche
    ctx.fillStyle = a.label_color;
    ctx.font = `${Math.max(14 / zoom, 10)}px sans-serif`;
    ctx.fillText(a.label_name, a.x0 + 4 / zoom, a.y0 + 18 / zoom);
  }
  const tBoxes = VERBOSE ? performance.now() : 0;

  // Mise en évidence des zones de chevauchement entre boîtes : on remplit en
  // rouge translucide chaque intersection. Plus c'est rouge, plus de boîtes se
  // superposent là (l'alpha s'accumule). Sert de guide visuel pour la fusion.
  // ⚠ O(n²) sur le nombre de boîtes : exécuté à chaque frame -> candidat n°1 au lag.
  if (highlightOverlaps) drawOverlapZones();
  const tOvl = VERBOSE ? performance.now() : 0;

  // Bbox en cours de dessin
  if (isDrawing && drawStart && drawCur) {
    const x0 = Math.min(drawStart.x, drawCur.x);
    const y0 = Math.min(drawStart.y, drawCur.y);
    const x1 = Math.max(drawStart.x, drawCur.x);
    const y1 = Math.max(drawStart.y, drawCur.y);
    const col = currentLabel ? currentLabel.color : '#ff0';
    drawRect(x0, y0, x1, y1, col, false, [6 / zoom, 4 / zoom]);
  }

  if (VERBOSE) {
    const total = performance.now() - t0;
    if (dragT0) { dragFrames++; if (total > dragWorstPaint) dragWorstPaint = total; }
    // On ne loggue que les frames qui dépassent le budget, pour ne pas noyer la console.
    if (total >= FRAME_BUDGET) {
      vwarn(`paint ${total.toFixed(1)} ms ` +
            `(boîtes ${(tBoxes - t0).toFixed(1)} + chevauch. ${(tOvl - tBoxes).toFixed(1)}) ` +
            `— ${annotations.length} boîtes, ${lastOverlapPairs} paires surlignées, zoom ${(zoom*100).toFixed(0)}%`);
    }
  }
}

function drawRect(x0, y0, x1, y1, color, selected, dash) {
  ctx.save();
  // Remplissage transparent
  ctx.fillStyle = hexToRgba(color, selected ? 0.25 : 0.15);
  ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
  // Bord
  ctx.strokeStyle = color;
  ctx.lineWidth = (selected ? 3 : 2) / zoom;
  if (dash) ctx.setLineDash(dash);
  ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
  // Poignees aux 4 coins si selectionne
  if (selected) {
    ctx.fillStyle = '#fff';
    ctx.setLineDash([]);
    const h = HANDLE / zoom;
    for (const [hx, hy] of [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) {
      ctx.fillRect(hx - h / 2, hy - h / 2, h, h);
      ctx.strokeRect(hx - h / 2, hy - h / 2, h, h);
    }
  }
  ctx.restore();
}

function hexToRgba(hex, alpha) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!m) return `rgba(255,255,0,${alpha})`;
  return `rgba(${parseInt(m[1],16)},${parseInt(m[2],16)},${parseInt(m[3],16)},${alpha})`;
}

// ── Géométrie des boîtes (chevauchement / fusion) ──
function boxIntersection(a, b) {
  const x0 = Math.max(a.x0, b.x0), y0 = Math.max(a.y0, b.y0);
  const x1 = Math.min(a.x1, b.x1), y1 = Math.min(a.y1, b.y1);
  if (x1 <= x0 || y1 <= y0) return null;
  return { x0, y0, x1, y1 };
}

// Recouvrement relatif à la PLUS PETITE boîte : intersection / min(aire).
// ~1 si l'une est presque entièrement dans l'autre (même critère que le
// post-traitement côté serveur dans suggest.py).
function boxOverlapMin(a, b) {
  const r = boxIntersection(a, b);
  if (!r) return 0;
  const inter = (r.x1 - r.x0) * (r.y1 - r.y0);
  const areaA = (a.x1 - a.x0) * (a.y1 - a.y0);
  const areaB = (b.x1 - b.x0) * (b.y1 - b.y0);
  const sm = Math.min(areaA, areaB);
  return sm > 0 ? inter / sm : 0;
}

// Remplit chaque intersection de paire de boîtes en rouge translucide.
let lastOverlapPairs = 0;   // nb de zones réellement dessinées (suivi perf)
function drawOverlapZones() {
  ctx.save();
  ctx.fillStyle = 'rgba(255,45,45,0.30)';
  ctx.strokeStyle = 'rgba(220,0,0,0.85)';
  ctx.lineWidth = 1 / zoom;
  ctx.setLineDash([5 / zoom, 4 / zoom]);
  let pairs = 0;
  for (let i = 0; i < annotations.length; i++) {
    for (let j = i + 1; j < annotations.length; j++) {
      const r = boxIntersection(annotations[i], annotations[j]);
      if (!r) continue;
      const w = r.x1 - r.x0, h = r.y1 - r.y0;
      if (w * h < 4) continue;             // ignore les slivers de 1-2 px
      ctx.fillRect(r.x0, r.y0, w, h);
      ctx.strokeRect(r.x0, r.y0, w, h);
      pairs++;
    }
  }
  lastOverlapPairs = pairs;
  ctx.restore();
}

// ────────────────────────────────────────────────────────────
// Souris
// ────────────────────────────────────────────────────────────
stage.addEventListener('mousedown', e => {
  if (e.button === 2) {                 // right click : pan
    isPanning = true;
    panStart  = { x: e.clientX - panX, y: e.clientY - panY };
    stage.style.cursor = 'grabbing';
    startFrameMonitor('pan');
    return;
  }
  const p = screenToImage(e.clientX, e.clientY);
  // Si on clique sur une poignee d'une bbox selectionnee
  if (selectedId) {
    const a = annotations.find(a => a.id === selectedId);
    const h = HANDLE / zoom;
    for (const [name, hx, hy] of [
      ['nw', a.x0, a.y0], ['ne', a.x1, a.y0],
      ['sw', a.x0, a.y1], ['se', a.x1, a.y1],
    ]) {
      if (Math.abs(p.x - hx) < h && Math.abs(p.y - hy) < h) {
        resizeMode = name;
        dragBefore = { id: a.id, x0: a.x0, y0: a.y0, x1: a.x1, y1: a.y1 };
        dragPerfStart();
        return;
      }
    }
    // Drag de la boite ?
    if (p.x >= a.x0 && p.x <= a.x1 && p.y >= a.y0 && p.y <= a.y1) {
      dragOffset = { dx: p.x - a.x0, dy: p.y - a.y0,
                      w: a.x1 - a.x0, h: a.y1 - a.y0 };
      dragBefore = { id: a.id, x0: a.x0, y0: a.y0, x1: a.x1, y1: a.y1 };
      dragPerfStart();
      return;
    }
  }
  // Clic sur une autre bbox : la selectionner
  const hit = pickAt(p.x, p.y);
  if (hit) {
    selectAnnotation(hit.id);
    return;
  }
  // Sinon : commencer un nouveau dessin
  if (!currentLabel) {
    statusEl.textContent = 'Selectionne d\'abord un label';
    return;
  }
  isDrawing = true;
  drawStart = p;
  drawCur   = p;
  selectedId = null;
  dragPerfStart();
  refreshAnnoList();
  render();
});

// Applique l'action en cours (dessin / resize / drag) à une position écran
// donnée. Factorisé pour être réutilisé par l'auto-scroll de bord.
function applyDragAt(clientX, clientY) {
  const p = screenToImage(clientX, clientY);
  if (isDrawing) { drawCur = p; render(); return; }
  if (resizeMode && selectedId) {
    const a = annotations.find(a => a.id === selectedId);
    if (resizeMode.includes('w')) a.x0 = Math.min(p.x, a.x1 - 5);
    if (resizeMode.includes('e')) a.x1 = Math.max(p.x, a.x0 + 5);
    if (resizeMode.includes('n')) a.y0 = Math.min(p.y, a.y1 - 5);
    if (resizeMode.includes('s')) a.y1 = Math.max(p.y, a.y0 + 5);
    render();
    return;
  }
  if (dragOffset && selectedId) {
    const a = annotations.find(a => a.id === selectedId);
    a.x0 = p.x - dragOffset.dx;
    a.y0 = p.y - dragOffset.dy;
    a.x1 = a.x0 + dragOffset.w;
    a.y1 = a.y0 + dragOffset.h;
    render();
    return;
  }
}

// Détermine la direction de défilement selon la proximité des bords du stage.
function updateAutoPan(e) {
  const rect = stage.getBoundingClientRect();
  let dx = 0, dy = 0;
  if (e.clientX < rect.left + EDGE_ZONE)        dx =  PAN_STEP;  // bord gauche
  else if (e.clientX > rect.right - EDGE_ZONE)  dx = -PAN_STEP;  // bord droit
  if (e.clientY < rect.top + EDGE_ZONE)         dy =  PAN_STEP;  // bord haut
  else if (e.clientY > rect.bottom - EDGE_ZONE) dy = -PAN_STEP;  // bord bas
  autoPan = { dx, dy };
  if ((dx || dy) && isDragging()) startAutoPan(); else stopAutoPan();
}

function startAutoPan() {
  if (autoPanRAF) return;
  const tick = () => {
    if (!isDragging() || (!autoPan.dx && !autoPan.dy)) { autoPanRAF = null; return; }
    panX += autoPan.dx; panY += autoPan.dy;
    applyTransform();
    // La souris reste fixe à l'écran : en défilant, le même point écran couvre
    // une nouvelle zone image -> on prolonge le dessin/déplacement dans ce sens.
    applyDragAt(lastClient.x, lastClient.y);
    autoPanRAF = requestAnimationFrame(tick);
  };
  autoPanRAF = requestAnimationFrame(tick);
}

function stopAutoPan() {
  if (autoPanRAF) { cancelAnimationFrame(autoPanRAF); autoPanRAF = null; }
  autoPan = { dx: 0, dy: 0 };
}

stage.addEventListener('mousemove', e => {
  const p = screenToImage(e.clientX, e.clientY);
  coordEl.textContent = `(${Math.round(p.x)}, ${Math.round(p.y)})`;

  if (isPanning) {
    panX = e.clientX - panStart.x;
    panY = e.clientY - panStart.y;
    applyTransform();
    return;
  }
  if (isDragging()) {
    lastClient = { x: e.clientX, y: e.clientY };
    applyDragAt(e.clientX, e.clientY);
    updateAutoPan(e);
  }
});

stage.addEventListener('mouseup', async e => {
  if (e.button === 2) { isPanning = false; stage.style.cursor = ''; stopFrameMonitor(); return; }
  stopAutoPan();
  if (isDragging()) dragPerfEnd(isDrawing ? 'dessin' : resizeMode ? 'resize' : 'déplacement');
  if (isDrawing) {
    isDrawing = false;
    const x0 = Math.min(drawStart.x, drawCur.x);
    const y0 = Math.min(drawStart.y, drawCur.y);
    const x1 = Math.max(drawStart.x, drawCur.x);
    const y1 = Math.max(drawStart.y, drawCur.y);
    if (x1 - x0 > 5 && y1 - y0 > 5 && currentLabel) {
      // Affichage optimiste : la boîte apparaît tout de suite (id temporaire),
      // la persistance réseau se fait en arrière-plan. Évite de figer l'écran
      // sur la boîte pointillée le temps des allers-retours serveur.
      const box = {
        id: 'tmp-' + (++tmpSeq),
        label_id: currentLabel.id,
        label_name: currentLabel.name,
        label_color: currentLabel.color,
        x0: Math.round(x0), y0: Math.round(y0), x1: Math.round(x1), y1: Math.round(y1),
      };
      annotations.push(box);
      selectedId = box.id;
      refreshAnnoList();
      render();                       // feedback instantané
      vfetch('/api/annotations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_id: imageId, label_id: box.label_id,
          x0: box.x0, y0: box.y0, x1: box.x1, y1: box.y1,
        }),
      }).then(r => r.json()).then(r => {
        if (selectedId === box.id) selectedId = r.id;
        box.id = r.id;                // l'id temporaire devient l'id réel
        pushUndo(async () => {
          await fetch(`/api/annotations/${r.id}`, { method: 'DELETE' });
          if (selectedId === r.id) selectedId = null;
          await reloadAnnotations();
        });
        refreshAnnoList();            // rebranche le bon id sur le <li>
      });
    } else {
      render();
    }
  }
  if (resizeMode || dragOffset) {
    // Sauvegarder modification
    const a = annotations.find(a => a.id === selectedId);
    if (a) {
      await fetch(`/api/annotations/${a.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          x0: Math.round(a.x0), y0: Math.round(a.y0),
          x1: Math.round(a.x1), y1: Math.round(a.y1),
        }),
      });
      // Undo : ne l'enregistrer que si la boîte a réellement changé
      const s = dragBefore;
      if (s && s.id === a.id &&
          (s.x0 !== Math.round(a.x0) || s.y0 !== Math.round(a.y0) ||
           s.x1 !== Math.round(a.x1) || s.y1 !== Math.round(a.y1))) {
        pushUndo(async () => {
          await fetch(`/api/annotations/${s.id}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x0: s.x0, y0: s.y0, x1: s.x1, y1: s.y1 }),
          });
          await reloadAnnotations();
        });
      }
    }
    resizeMode = null;
    dragOffset = null;
    dragBefore = null;
  }
});

stage.addEventListener('contextmenu', e => e.preventDefault());

stage.addEventListener('wheel', e => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  const rect = stage.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  // Zoom autour du curseur
  const beforeX = (mx - panX) / zoom;
  const beforeY = (my - panY) / zoom;
  zoom *= factor;
  zoom = Math.max(0.05, Math.min(zoom, 8));
  panX = mx - beforeX * zoom;
  panY = my - beforeY * zoom;
  applyTransform();
  statusEl.textContent = `Zoom ${(zoom*100).toFixed(0)}%`;
  zoomMonitorPing();
});

// ────────────────────────────────────────────────────────────
// Clavier
// ────────────────────────────────────────────────────────────

// Renvoie le chiffre (0-9) de la touche physique pressee, independamment de
// la disposition clavier. e.code vaut Digit1..Digit9/Digit0 (rangee du haut)
// ou Numpad0..Numpad9 (pave numerique). Fallback sur e.key. null sinon.
function digitFromEvent(e) {
  const m = /^(?:Digit|Numpad)([0-9])$/.exec(e.code || '');
  if (m) return parseInt(m[1]);
  if (/^[0-9]$/.test(e.key)) return parseInt(e.key);
  return null;
}

document.addEventListener('keydown', async e => {
  if (e.target.tagName === 'INPUT') return;
  // Modale de révision ouverte : on neutralise les raccourcis d'annotation
  // (sauf Échap pour fermer et Ctrl+Z pour annuler).
  if (reviewModal && reviewModal.style.display !== 'none') {
    if (e.key === 'Escape') { reviewModal.style.display = 'none'; return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); await undo(); }
    return;
  }
  // Ctrl+Z / Cmd+Z : annuler la derniere action
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
    e.preventDefault();
    await undo();
    return;
  }
  // Rangee numerique (et pave num.) : selectionne le label -> reclasse aussi
  // la boite selectionnee (cf. selectLabel). On lit e.code pour etre independant
  // de la disposition : sur AZERTY la rangee du haut non-shiftee donne &,é,",',(
  // dans e.key, mais Digit1..Digit9 dans e.code. 0 = 10e label.
  if (!e.ctrlKey && !e.metaKey && !e.altKey) {
    const d = digitFromEvent(e);
    if (d !== null) {
      const idx = d === 0 ? 9 : d - 1;
      if (idx < labels.length) selectLabel(labels[idx].id);
      e.preventDefault();
      return;
    }
  }
  if (e.key === 'Escape') {
    isDrawing = false;
    selectedId = null;
    refreshAnnoList();
    render();
  }
  if ((e.key === 'Delete' || e.key === 'Backspace') && selectedId) {
    e.preventDefault();
    const a = annotations.find(a => a.id === selectedId);
    if (a) await deleteAnnotationWithUndo(a);
  }
  if (e.key === 'f') fitToView();
  if (e.key === 'h') {
    highlightOverlaps = !highlightOverlaps;
    statusEl.textContent = `Surlignage des chevauchements ${highlightOverlaps ? 'activé' : 'désactivé'}`;
    render();
  }
  if (e.key === 'v') {
    VERBOSE = !VERBOSE;
    localStorage.setItem('annot_verbose', VERBOSE ? '1' : '0');
    statusEl.textContent = `Mode verbose ${VERBOSE ? 'activé' : 'désactivé'} — voir la console (F12)`;
    if (VERBOSE) logEnvInfo();
  }
});

// Contexte utile au diagnostic : taille image (scan), taille du canvas overlay
// (désormais = viewport, pas l'image), nb de boîtes.
function logEnvInfo() {
  const mpx = (IMG_W * IMG_H / 1e6).toFixed(1);
  const cmpx = (canvas.width * canvas.height / 1e6).toFixed(1);
  vlog(`image ${IMG_W}×${IMG_H} (${mpx} Mpx) | canvas overlay ${canvas.width}×${canvas.height} ` +
       `(${cmpx} Mpx) | ${annotations.length} boîtes | zoom ${(zoom*100).toFixed(0)}% | dpr ${window.devicePixelRatio}`);
}

// ────────────────────────────────────────────────────────────
// Labels panel
// ────────────────────────────────────────────────────────────
labelsEl.addEventListener('click', e => {
  const li = e.target.closest('li[data-label-id]');
  if (!li) return;
  selectLabel(parseInt(li.dataset.labelId));
});

function selectLabel(id) {
  currentLabel = labelById[id];
  for (const li of labelsEl.querySelectorAll('li')) {
    li.classList.toggle('active', parseInt(li.dataset.labelId) === id);
  }
  // Si une annotation est selectionnee : changer son label
  if (selectedId) {
    const a = annotations.find(a => a.id === selectedId);
    if (a && a.label_id !== id) {
      const annoId = a.id;
      const beforeLabel = a.label_id;
      a.label_id = id;
      a.label_name = currentLabel.name;
      a.label_color = currentLabel.color;
      fetch(`/api/annotations/${annoId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          x0: a.x0, y0: a.y0, x1: a.x1, y1: a.y1,
          label_id: id,
        }),
      }).then(() => {
        pushUndo(async () => {
          const cur = annotations.find(x => x.id === annoId) || a;
          await fetch(`/api/annotations/${annoId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              x0: cur.x0, y0: cur.y0, x1: cur.x1, y1: cur.y1, label_id: beforeLabel,
            }),
          });
          await reloadAnnotations();
        });
        refreshAnnoList();
        render();
      });
    }
  }
}

// ────────────────────────────────────────────────────────────
// Liste des annotations
// ────────────────────────────────────────────────────────────
function refreshAnnoList() {
  annoListEl.innerHTML = '';
  for (const a of annotations) {
    const li = document.createElement('li');
    if (a.id === selectedId) li.classList.add('selected');
    li.dataset.annoId = a.id;
    li.innerHTML = `
      <span class="swatch" style="background:${a.label_color}"></span>
      <span>${a.label_name}</span>
      <span style="color:#888;font-size:10px;">${a.x0},${a.y0} → ${a.x1},${a.y1}</span>
      <button class="del">×</button>
    `;
    li.addEventListener('click', e => {
      if (e.target.classList.contains('del')) {
        deleteAnnotationWithUndo(a);
      } else {
        selectAnnotation(a.id);
      }
    });
    annoListEl.appendChild(li);
  }
}

function selectAnnotation(id) {
  selectedId = id;
  refreshAnnoList();
  render();
}

// Supprime une annotation en empilant un undo qui la recrée.
async function deleteAnnotationWithUndo(a) {
  const snap = { label_id: a.label_id, x0: a.x0, y0: a.y0, x1: a.x1, y1: a.y1 };
  await fetch(`/api/annotations/${a.id}`, { method: 'DELETE' });
  pushUndo(async () => {
    const r = await fetch('/api/annotations', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_id: imageId, ...snap }),
    }).then(r => r.json());
    selectedId = r.id;
    await reloadAnnotations();
  });
  if (selectedId === a.id) selectedId = null;
  await reloadAnnotations();
}

// Supprime toutes les annotations de l'une (avec confirmation + undo groupé).
const clearAllBtn = document.getElementById('btn-clear-all');
async function clearAllAnnotations() {
  if (!annotations.length) { statusEl.textContent = 'Aucune annotation à supprimer.'; return; }
  const n = annotations.length;
  if (!confirm(`Supprimer les ${n} annotations de cette une ?`)) return;
  const snap = annotations.map(a => ({ label_id: a.label_id, x0: a.x0, y0: a.y0, x1: a.x1, y1: a.y1 }));
  clearAllBtn.disabled = true;
  try {
    for (const a of annotations) await fetch(`/api/annotations/${a.id}`, { method: 'DELETE' });
    pushUndo(async () => {
      for (const s of snap) {
        await fetch('/api/annotations', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_id: imageId, ...s }),
        });
      }
      await reloadAnnotations();
    });
    selectedId = null;
    merged = false; mergeInfo = null; updateMergeBtn();   // l'état de fusion n'a plus de sens
    await reloadAnnotations();
    statusEl.textContent = `${n} annotations supprimées — Ctrl+Z pour annuler.`;
  } finally {
    clearAllBtn.disabled = false;
  }
}
clearAllBtn.addEventListener('click', clearAllAnnotations);

// ────────────────────────────────────────────────────────────
// Mode révision : propositions de correction (avant / après)
// ────────────────────────────────────────────────────────────
const reviewBtn = document.getElementById('btn-review');
const reviewModal = document.getElementById('review-modal');
const reviewCards = document.getElementById('review-cards');
const reviewTitle = document.getElementById('review-title');
document.getElementById('review-close').addEventListener('click', () => { reviewModal.style.display = 'none'; });

reviewBtn.addEventListener('click', async () => {
  const old = reviewBtn.textContent;
  reviewBtn.disabled = true; reviewBtn.textContent = '⏳ Analyse… (~10s)';
  statusEl.textContent = 'Calcul des propositions de correction…';
  try {
    const r = await fetch(`/api/image/${imageId}/review/compute`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const data = await r.json();
    if (!r.ok) { statusEl.textContent = 'Erreur révision : ' + (data.error || r.status); return; }
    const props = await fetch(`/api/image/${imageId}/proposals`).then(r => r.json());
    openReviewModal(props);
    statusEl.textContent = `${props.length} proposition(s) de correction`;
  } catch (e) {
    statusEl.textContent = 'Erreur révision : ' + e;
  } finally {
    reviewBtn.disabled = false; reviewBtn.textContent = old;
  }
});

function openReviewModal(props) {
  reviewTitle.textContent = `Révision — ${props.length} proposition(s)`;
  reviewCards.innerHTML = '';
  if (!props.length) {
    reviewCards.innerHTML = '<div class="review-empty">Aucune proposition : la convention semble déjà respectée sur cette une. 👍</div>';
  } else {
    for (const p of props) reviewCards.appendChild(makeCard(p));
  }
  reviewModal.style.display = 'flex';
}

function makeCard(p) {
  const pl = p.payload;
  const card = document.createElement('div');
  card.className = `review-card type-${p.ptype}`;
  card.dataset.pid = p.id;
  const badge = p.ptype === 'reclassify' ? 'reclasser' : 'titre';
  card.innerHTML = `
    <div class="rc-head">
      <span class="rc-badge">${badge}</span>
      <span class="rc-desc"></span>
      <span class="rc-conf">conf ${pl.conf ?? '—'}</span>
      <div class="rc-actions">
        <button class="ok act-accept">✅ Accepter</button>
        <button class="act-correct" style="background:#e9a23b;">✏️ Corriger</button>
        <button class="warn act-reject">❌ Refuser</button>
      </div>
    </div>
    <div class="rc-views">
      <div class="rc-view"><span>Actuel</span><canvas class="mini-before"></canvas></div>
      <div class="rc-view"><span>Proposé</span><canvas class="mini-after"></canvas></div>
    </div>`;
  card.querySelector('.rc-desc').textContent = p.descr;   // textContent = pas d'injection HTML
  drawMini(card.querySelector('.mini-before'), pl.region, pl.before, true);
  drawMini(card.querySelector('.mini-after'), pl.region, pl.after, false);
  card.querySelector('.act-accept').addEventListener('click', () => applyProposal(p.id, false, card, pl.region));
  card.querySelector('.act-correct').addEventListener('click', () => applyProposal(p.id, true, card, pl.region));
  card.querySelector('.act-reject').addEventListener('click', () => rejectProposal(p.id, card));
  return card;
}

// Dessine un crop de la zone concernée + les boîtes (avant ou après).
function drawMini(cv, region, boxes, dashed) {
  const [rx0, ry0, rx1, ry1] = region;
  const rw = Math.max(1, rx1 - rx0), rh = Math.max(1, ry1 - ry0);
  const scale = Math.min(300 / rw, 340 / rh);
  cv.width = Math.round(rw * scale); cv.height = Math.round(rh * scale);
  const c = cv.getContext('2d');
  try { c.drawImage(bgImg, rx0, ry0, rw, rh, 0, 0, cv.width, cv.height); }
  catch (e) { c.fillStyle = '#222'; c.fillRect(0, 0, cv.width, cv.height); }
  // contexte : autres annotations présentes dans la zone (gris fin)
  for (const a of annotations) {
    if (a.x1 < rx0 || a.x0 > rx1 || a.y1 < ry0 || a.y0 > ry1) continue;
    drawMiniBox(c, a, rx0, ry0, scale, 'rgba(255,255,255,0.22)', 1, null, false);
  }
  for (const b of boxes) {
    const col = (labelById[b.label_id] && labelById[b.label_id].color) || '#ffffff';
    drawMiniBox(c, b, rx0, ry0, scale, col, 2.5, b.label_name, dashed);
  }
}

function drawMiniBox(c, b, rx0, ry0, scale, color, lw, label, dashed) {
  const x = (b.x0-rx0)*scale, y = (b.y0-ry0)*scale, w = (b.x1-b.x0)*scale, h = (b.y1-b.y0)*scale;
  c.save();
  c.strokeStyle = color; c.lineWidth = lw;
  if (dashed) c.setLineDash([5, 3]);
  c.strokeRect(x, y, w, h);
  if (label) {
    c.setLineDash([]); c.font = '11px sans-serif';
    const tw = c.measureText(label).width;
    c.fillStyle = color; c.fillRect(x, Math.max(0, y - 13), tw + 6, 13);
    c.fillStyle = '#000'; c.fillText(label, x + 3, Math.max(9, y - 3));
  }
  c.restore();
}

async function applyProposal(pid, correct, card, region) {
  const r = await fetch(`/api/proposals/${pid}/apply`, { method: 'POST' }).then(r => r.json());
  if (r.error) { statusEl.textContent = r.error; return; }
  pushUndo(async () => {
    for (const c of r.created) await fetch(`/api/annotations/${c.id}`, { method: 'DELETE' });
    for (const d of r.deleted) {
      await fetch('/api/annotations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_id: imageId, label_id: d.label_id, x0: d.x0, y0: d.y0, x1: d.x1, y1: d.y1 }),
      });
    }
    await reloadAnnotations();
  });
  await reloadAnnotations();
  card.remove();
  if (correct) {
    // Corriger : fermer la modale, cadrer la vue sur la zone et sélectionner
    // la 1re boîte créée pour l'ajuster tout de suite.
    reviewModal.style.display = 'none';
    if (r.created.length) selectedId = r.created[0].id;
    refreshAnnoList();
    if (region) zoomToImageRegion(region[0], region[1], region[2], region[3]);
    else render();
    statusEl.textContent = 'Zone à corriger — ajuste les boîtes puis reviens à la révision';
  }
  afterCardRemoved();
}

async function rejectProposal(pid, card) {
  await fetch(`/api/proposals/${pid}/reject`, { method: 'POST' });
  card.remove();
  afterCardRemoved();
}

function afterCardRemoved() {
  const n = reviewCards.querySelectorAll('.review-card').length;
  reviewTitle.textContent = `Révision — ${n} proposition(s)`;
  if (!n) reviewCards.innerHTML = '<div class="review-empty">Terminé pour cette une. 👍</div>';
}

document.getElementById('review-reject-all').addEventListener('click', async () => {
  for (const card of [...reviewCards.querySelectorAll('.review-card')]) {
    await rejectProposal(parseInt(card.dataset.pid), card);
  }
});

function pickAt(x, y) {
  // Boite la plus PETITE qui contient le point (priorite a l'innermost)
  const hits = annotations.filter(a =>
    x >= a.x0 && x <= a.x1 && y >= a.y0 && y <= a.y1);
  if (!hits.length) return null;
  hits.sort((a, b) => (a.x1 - a.x0) * (a.y1 - a.y0) - (b.x1 - b.x0) * (b.y1 - b.y0));
  return hits[0];
}

// ────────────────────────────────────────────────────────────
// Boutons header
// ────────────────────────────────────────────────────────────
document.getElementById('btn-save').addEventListener('click', async () => {
  await fetch(`/api/image/${imageId}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'done' }),
  });
  window.location.href = '/';
});

document.getElementById('btn-skip').addEventListener('click', async () => {
  await fetch(`/api/image/${imageId}/status`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'skipped' }),
  });
  window.location.href = '/';
});

init();
