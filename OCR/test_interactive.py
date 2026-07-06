"""
Page de test interactive : PaddleOCR blocs → OCR Kraken vs Tesseract sur sélection.

Usage :
    conda run -n oldspapers python OCR/test_interactive.py
    → http://localhost:8766

Modes :
  • Mode bloc  : cliquer sur un bloc détecté par PaddleOCR
  • Mode dessin: tracer un rectangle libre sur l'image
→ Dans les deux cas : Tesseract + Kraken lancés en parallèle, résultats côte à côte.
"""
import http.server
import json
import pathlib
import sys
import threading
import time
import urllib.parse
import webbrowser

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

IMG_CACHE_DIR = ROOT / "cache" / "ocr_img"
OCR_CACHE_DIR = ROOT / "cache" / "ocr"
PORT = 8766

from OCR.ocr_local import (
    run_layout_blocks, run_ocr_region, run_ocr_region_kraken,
    HAS_TESSERACT, HAS_PADDLE, HAS_KRAKEN, _KRAKEN_MODEL_PATH,
)

# =============================================================================
# PAGE HTML EMBARQUÉE
# =============================================================================

HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Test OCR interactif</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  height: 100%;
  background: #111;
  color: #ddd;
  font-family: ui-monospace, "Cascadia Code", "Consolas", monospace;
  overflow: hidden;
}

/* ── Header ───────────────────────────────────────────── */
#hdr {
  height: 42px;
  background: #1c1c2e;
  border-bottom: 1px solid #333;
  display: flex;
  align-items: center;
  padding: 0 12px;
  gap: 8px;
  flex-shrink: 0;
}
#hdr b { color: #aad4ff; font-size: 14px; margin-right: 4px; }
select {
  background: #2a2a4a; border: 1px solid #444; color: #ddd;
  padding: 3px 8px; font-size: 12px; border-radius: 3px; cursor: pointer;
}
.btn {
  background: #2a2a4a; border: 1px solid #444; color: #ddd;
  padding: 4px 10px; font-size: 12px; border-radius: 3px; cursor: pointer;
}
.btn:hover { background: #3a3a60; }
.btn.on { background: #5a2080; border-color: #aa88cc; }
#caps { margin-left: auto; color: #555; font-size: 11px; white-space: nowrap; }

/* ── Body ─────────────────────────────────────────────── */
#body {
  display: flex;
  height: calc(100vh - 42px - 24px);
}

/* ── Image panel (left, 58%) ──────────────────────────── */
#lpanel {
  flex: 58;
  overflow-y: auto;
  overflow-x: hidden;
  background: #1a1a2e;
  border-right: 2px solid #333;
}
#imgwrap {
  position: relative;
  width: 100%;
  user-select: none;
}
#imgwrap img {
  display: block;
  width: 100%;
  height: auto;
}
.blk {
  position: absolute;
  border: 2px solid;
  opacity: 0.28;
  cursor: pointer;
  transition: opacity .1s, background .1s;
}
.blk:hover { opacity: 0.72; }
.blk.sel   { opacity: 0.88; z-index: 2; }

/* Draw mode : blocs non cliquables, curseur croix */
body.draw .blk { pointer-events: none; }
body.draw #imgwrap { cursor: crosshair; }

#selbox {
  position: absolute;
  border: 2.5px dashed #fff;
  background: rgba(255,255,255,.07);
  pointer-events: none;
  display: none;
  z-index: 10;
}

/* ── Results panel (right, 42%) ───────────────────────── */
#rpanel {
  flex: 42;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 260px;
}
.rpane {
  flex: 1;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid #2a2a3e;
  overflow: hidden;
  min-height: 0;
}
.rpane:last-child { border-bottom: none; }
.rpane-hd {
  flex-shrink: 0;
  background: #181828;
  border-bottom: 1px solid #2a2a3e;
  padding: 5px 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.rpane-hd .eng  { color: #aad4ff; font-weight: bold; font-size: 12px; }
.rpane-hd .meta { color: #555; font-size: 11px; }
.rpane-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 10px;
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}
.rpane-body.hint { color: #444; }
.rpane-body.wait { color: #777; font-style: italic; }
.rpane-body.err  { color: #cc6060; }
.rpane-body.ok   { color: #ddd; }

/* ── Status bar ───────────────────────────────────────── */
#sb {
  height: 24px;
  background: #0d0d1a;
  border-top: 1px solid #222;
  display: flex;
  align-items: center;
  padding: 0 12px;
  font-size: 11px;
  color: #555;
  flex-shrink: 0;
  overflow: hidden;
  white-space: nowrap;
}
</style>
</head>
<body>

<div id="hdr">
  <b>Test OCR</b>
  <select id="ark-sel"></select>
  <button class="btn" onclick="loadLayout()">⟳ Charger les blocs</button>
  <button class="btn" id="mode-btn" onclick="toggleMode()">✦ Mode bloc</button>
  <span id="caps"></span>
</div>

<div id="body">

  <!-- ── Image + overlay ── -->
  <div id="lpanel">
    <div id="imgwrap">
      <img id="img" alt="(aucune image)" style="display:none">
      <div id="selbox"></div>
    </div>
  </div>

  <!-- ── Résultats OCR ── -->
  <div id="rpanel">

    <div class="rpane">
      <div class="rpane-hd">
        <span class="eng">PaddleOCR</span>
        <span class="meta" id="pm">texte du bloc détecté</span>
      </div>
      <div class="rpane-body hint" id="pt">Cliquez sur un bloc…</div>
    </div>

    <div class="rpane">
      <div class="rpane-hd">
        <span class="eng">Tesseract (fra)</span>
        <span class="meta" id="tm"></span>
      </div>
      <div class="rpane-body hint" id="tt">Cliquez sur un bloc…</div>
    </div>

    <div class="rpane">
      <div class="rpane-hd">
        <span class="eng">Kraken CATMuS-Print</span>
        <span class="meta" id="km"></span>
      </div>
      <div class="rpane-body hint" id="kt">Cliquez sur un bloc…<br>(1ère fois : chargement modèle ~10s)</div>
    </div>

  </div>
</div>

<div id="sb">Sélectionnez un ARK et chargez les blocs.</div>

<script>
'use strict';

let ark = null, blocks = [], drawMode = false;
let drawing = false, ds = null;

const COLORS = [
  '#c0392b','#d35400','#b8a000','#27ae60',
  '#16a085','#2471a3','#7d3c98','#c0392b',
];

// ── Init ──────────────────────────────────────────────────────────────────────
Promise.all([
  fetch('/api/arks').then(r => r.json()),
  fetch('/api/caps').then(r => r.json()),
]).then(([arks, caps]) => {

  const sel = document.getElementById('ark-sel');
  arks.forEach(a => {
    const o = document.createElement('option');
    o.value = o.textContent = a;
    sel.appendChild(o);
  });
  const def = arks.find(a => a === 'bpt6k412758h') || arks[0];
  if (def) { sel.value = def; setImg(def); }

  document.getElementById('caps').textContent =
    'Kraken ' + (caps.kraken    ? '✓' : '✗') +
    '  Tess '  + (caps.tesseract ? '✓' : '✗') +
    '  Paddle ' + (caps.paddle   ? '✓' : '✗');

}).catch(e => setStatus('Erreur init : ' + e));

document.getElementById('ark-sel').addEventListener('change', e => {
  setImg(e.target.value);
  clearBlocks();
});

function setImg(a) {
  ark = a;
  const img = document.getElementById('img');
  img.style.display = '';
  img.src = '/api/image?ark=' + a;
}

function clearBlocks() {
  document.querySelectorAll('.blk').forEach(el => el.remove());
  blocks = [];
}

// ── Charger les blocs PaddleOCR ───────────────────────────────────────────────
async function loadLayout() {
  const a = document.getElementById('ark-sel').value;
  if (!a) return;
  clearBlocks();
  ark = a;
  setImg(a);
  setStatus('Chargement des blocs PaddleOCR…');

  try {
    const d = await fetch('/api/layout?ark=' + a).then(r => r.json());
    if (d.error) { setStatus('Erreur layout : ' + d.error); return; }
    blocks = d.blocks;
    renderBlocks(blocks);
    setStatus(
      blocks.length + ' blocs  ·  ' + d.n_cols + ' cols  ·  ' +
      d.img_w + '×' + d.img_h + 'px  ·  ' + d.elapsed + 's' +
      (d.cached ? '  (cache)' : '') +
      '  —  cliquez un bloc ou passez en mode dessin'
    );
  } catch(e) { setStatus('Erreur réseau : ' + e); }
}

// ── Afficher les blocs en overlay ─────────────────────────────────────────────
function renderBlocks(bs) {
  const wrap = document.getElementById('imgwrap');
  bs.forEach((b, i) => {
    const d = document.createElement('div');
    d.className = 'blk';
    d.dataset.i = i;
    const c = COLORS[(b.col ?? i) % COLORS.length];
    Object.assign(d.style, {
      left:        (b.x0 * 100) + '%',
      top:         (b.y0 * 100) + '%',
      width:       ((b.x1 - b.x0) * 100) + '%',
      height:      ((b.y1 - b.y0) * 100) + '%',
      borderColor: c,
      background:  c + '22',
    });
    d.title = '#' + (i+1) + ' ' + b.label + '  conf=' + (+(b.confidence??0).toFixed(2));
    d.addEventListener('click', () => selectBlock(b, i));
    wrap.appendChild(d);
  });
}

// ── Sélection d'un bloc (clic) ────────────────────────────────────────────────
function selectBlock(b, i) {
  document.querySelectorAll('.blk').forEach(el => el.classList.remove('sel'));
  document.querySelector('.blk[data-i="' + i + '"]')?.classList.add('sel');

  setText('p', b.text || '(vide)', 'bloc #' + (i+1) + ' ' + b.label, 'ok');
  setStatus(
    'Bloc #' + (i+1) + '  ' + b.label +
    '  x=' + b.x0.toFixed(3) + '…' + b.x1.toFixed(3) +
    '  y=' + b.y0.toFixed(3) + '…' + b.y1.toFixed(3)
  );
  triggerOCR(b.x0, b.y0, b.x1, b.y1);
}

// ── Lancer OCR Tesseract + Kraken en parallèle ────────────────────────────────
function triggerOCR(x0, y0, x1, y1) {
  if (!ark) { setStatus('Chargez d\'abord un ARK.'); return; }
  const q = 'ark=' + ark + '&x0=' + x0 + '&y0=' + y0 + '&x1=' + x1 + '&y1=' + y1;

  setText('t', '⏳ en cours…', '', 'wait');
  setText('k', '⏳ en cours…  (1ère fois : ~10-30s)', '', 'wait');

  fetch('/api/ocr-region?engine=tesseract&' + q)
    .then(r => r.json())
    .then(d => showResult('t', d))
    .catch(e => setText('t', String(e), '', 'err'));

  fetch('/api/ocr-region?engine=kraken&' + q)
    .then(r => r.json())
    .then(d => showResult('k', d))
    .catch(e => setText('k', String(e), '', 'err'));
}

function showResult(id, d) {
  if (d.error) {
    setText(id, d.error, '', 'err');
  } else {
    const meta = (d.elapsed ? d.elapsed + 's' : '') +
                 (d.n_lines ? '  ·  ' + d.n_lines + ' lignes' : '');
    setText(id, d.text || '(vide)', meta, 'ok');
  }
}

function setText(id, text, meta, cls) {
  const bodies = { p:'pt', t:'tt', k:'kt' };
  const metas  = { p:'pm', t:'tm', k:'km' };
  const el = document.getElementById(bodies[id]);
  el.className = 'rpane-body ' + cls;
  el.textContent = text;
  if (metas[id]) document.getElementById(metas[id]).textContent = meta;
}

// ── Mode dessin ───────────────────────────────────────────────────────────────
function toggleMode() {
  drawMode = !drawMode;
  document.body.classList.toggle('draw', drawMode);
  const btn = document.getElementById('mode-btn');
  btn.classList.toggle('on', drawMode);
  btn.textContent = drawMode ? '✎ Mode dessin' : '✦ Mode bloc';
  setStatus(drawMode
    ? 'Mode dessin — tracez un rectangle sur l\'image pour OCR.'
    : 'Mode bloc — cliquez sur un bloc détecté.');
}

function relPos(e) {
  const r = document.getElementById('img').getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
    y: Math.max(0, Math.min(1, (e.clientY - r.top)  / r.height)),
  };
}

const imgwrap = document.getElementById('imgwrap');
const selbox  = document.getElementById('selbox');

imgwrap.addEventListener('mousedown', e => {
  if (!drawMode) return;
  drawing = true;
  ds = relPos(e);
  selbox.style.display = '';
  e.preventDefault();
});

window.addEventListener('mousemove', e => {
  if (!drawing) return;
  const p  = relPos(e);
  const x0 = Math.min(ds.x, p.x), y0 = Math.min(ds.y, p.y);
  const x1 = Math.max(ds.x, p.x), y1 = Math.max(ds.y, p.y);
  Object.assign(selbox.style, {
    left:   (x0 * 100) + '%',
    top:    (y0 * 100) + '%',
    width:  ((x1 - x0) * 100) + '%',
    height: ((y1 - y0) * 100) + '%',
  });
});

window.addEventListener('mouseup', e => {
  if (!drawing) return;
  drawing = false;
  const p  = relPos(e);
  const x0 = Math.min(ds.x, p.x), y0 = Math.min(ds.y, p.y);
  const x1 = Math.max(ds.x, p.x), y1 = Math.max(ds.y, p.y);

  if (x1 - x0 > 0.005 && y1 - y0 > 0.005) {
    setText('p', '(sélection libre — pas de texte PaddleOCR)', '', 'hint');
    setStatus(
      'Zone dessinée  x=' + x0.toFixed(3) + '…' + x1.toFixed(3) +
      '  y=' + y0.toFixed(3) + '…' + y1.toFixed(3)
    );
    triggerOCR(x0, y0, x1, y1);
  }
  setTimeout(() => { selbox.style.display = 'none'; }, 600);
});

// ── Status bar ────────────────────────────────────────────────────────────────
function setStatus(s) { document.getElementById('sb').textContent = s; }
</script>
</body>
</html>"""


# =============================================================================
# SERVEUR HTTP
# =============================================================================

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # on imprime manuellement

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        def q(k):
            return (qs.get(k) or [""])[0]

        def send(code, ctype, body):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(data):
            send(200, "application/json; charset=utf-8",
                 json.dumps(data, ensure_ascii=False))

        path = parsed.path

        if path == "/":
            send(200, "text/html; charset=utf-8", HTML)
            return

        if path == "/api/arks":
            arks = sorted(p.stem for p in IMG_CACHE_DIR.glob("*.jpg"))
            send_json(arks)
            return

        if path == "/api/caps":
            send_json({
                "kraken":    HAS_KRAKEN,
                "tesseract": HAS_TESSERACT,
                "paddle":    HAS_PADDLE,
            })
            return

        if path == "/api/image":
            ark = q("ark")
            img_path = IMG_CACHE_DIR / f"{ark}.jpg"
            if not img_path.exists():
                send(404, "text/plain", "not found")
                return
            send(200, "image/jpeg", img_path.read_bytes())
            return

        if path == "/api/layout":
            ark = q("ark")
            t0 = time.time()
            print(f"  layout {ark}…", end=" ", flush=True)
            result = run_layout_blocks(ark, OCR_CACHE_DIR, IMG_CACHE_DIR)
            result["elapsed"] = round(time.time() - t0, 2)
            n = len(result.get("blocks", []))
            cached = result.get("cached", False)
            print(f"{n} blocs {'(cache) ' if cached else ''}{result['elapsed']}s")
            send_json(result)
            return

        if path == "/api/ocr-region":
            ark = q("ark")
            engine = q("engine") or "tesseract"
            try:
                x0, y0 = float(q("x0")), float(q("y0"))
                x1, y1 = float(q("x1")), float(q("y1"))
            except ValueError:
                send_json({"error": "coordonnées invalides"})
                return
            t0 = time.time()
            print(
                f"  [{engine}] {ark}  [{x0:.3f},{y0:.3f} → {x1:.3f},{y1:.3f}]…",
                end=" ", flush=True,
            )
            if engine == "kraken":
                result = run_ocr_region_kraken(ark, x0, y0, x1, y1, IMG_CACHE_DIR)
            else:
                result = run_ocr_region(ark, x0, y0, x1, y1, IMG_CACHE_DIR)
            result["elapsed"] = round(time.time() - t0, 2)
            summary = result.get("error") or f"{len(result.get('text',''))} chars"
            print(f"→ {summary}  {result['elapsed']}s")
            send_json(result)
            return

        send(404, "text/plain", "not found")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("localhost", PORT), Handler)
    url = f"http://localhost:{PORT}"

    print(f"\nTest OCR interactif  →  {url}")
    print(f"  Kraken    : {'OK  (' + _KRAKEN_MODEL_PATH.name + ')' if HAS_KRAKEN else 'non disponible'}")
    print(f"  Tesseract : {'OK' if HAS_TESSERACT else 'non disponible'}")
    print(f"  Paddle    : {'OK' if HAS_PADDLE else 'non disponible'}")
    print(f"\nImages en cache : {', '.join(p.stem for p in sorted(IMG_CACHE_DIR.glob('*.jpg')))}")
    print("\nCtrl+C pour arrêter.\n")

    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
