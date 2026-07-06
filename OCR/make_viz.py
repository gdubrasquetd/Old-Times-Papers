"""
Génère un HTML de visualisation splitté :
  gauche  — image originale avec blocs colorés en overlay
  droite  — version numérisée : texte aux mêmes coordonnées (% purs, aspect-ratio CSS)

Usage : conda run -n oldspapers python OCR/make_viz.py [ARK]
"""
import json, pathlib, sys, base64

ROOT = pathlib.Path(__file__).parent.parent
ARK  = sys.argv[1] if len(sys.argv) > 1 else "bpt6k412758h"

layout_path = ROOT / "cache" / "ocr" / f"{ARK}_layout_paddle.json"
img_path    = ROOT / "cache" / "ocr_img" / f"{ARK}.jpg"
out_path    = ROOT / "cache" / "verify" / f"{ARK}_viz.html"
out_path.parent.mkdir(parents=True, exist_ok=True)

if not layout_path.exists():
    print(f"Pas de layout : {layout_path}"); sys.exit(1)
if not img_path.exists():
    print(f"Pas d'image : {img_path}"); sys.exit(1)

data   = json.loads(layout_path.read_text(encoding="utf-8"))
blocks = data["blocks"]
n_cols = data["n_cols"]
img_w  = data["img_w"]
img_h  = data["img_h"]

for b in blocks:
    if "col" not in b:
        b["col"] = round(b["x0"] * n_cols)

img_b64    = base64.b64encode(img_path.read_bytes()).decode()
blocks_js  = json.dumps(blocks, ensure_ascii=False)
col_colors = json.dumps([
    "#c0392b","#d35400","#b8860b","#27ae60",
    "#16a085","#2471a3","#7d3c98","#c0392b",
])
ratio = f"{img_w}/{img_h}"   # pour CSS aspect-ratio

html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Layout — {ARK}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ height: 100%; background: #111; color: #eee; font-family: monospace; overflow: hidden; }}

#header {{
  height: 34px; padding: 0 14px;
  display: flex; align-items: center; gap: 20px;
  background: #1c1c2e; border-bottom: 1px solid #333; font-size: 12px;
}}
#header b {{ font-size: 13px; }}
#header span {{ color: #999; }}

#panels {{
  display: flex;
  height: calc(100vh - 34px);
}}

/* ── Panneau gauche ───────────────────────────────── */
#left {{
  flex: 1; overflow-y: auto; overflow-x: hidden;
  background: #1a1a2e; border-right: 2px solid #444;
}}
#img-wrap {{
  position: relative;
  width: 100%;
}}
#img-wrap img {{
  display: block; width: 100%; height: auto;
}}
.box {{
  position: absolute;
  border: 2px solid;
  opacity: .35;
  cursor: pointer;
  transition: opacity .12s;
}}
.box:hover, .box.hi {{ opacity: .85; z-index: 5; }}

/* ── Panneau droit ────────────────────────────────── */
#right {{
  flex: 1; overflow-y: auto; overflow-x: hidden;
  background: #f5f0e8;
}}
#page-right {{
  position: relative;
  width: 100%;
  aspect-ratio: {ratio};
  background: #fdf8f0;
}}
.tb {{
  position: absolute;
  overflow: hidden;
  cursor: pointer;
  border: 1.5px solid transparent;
  padding: 1px 2px;
}}
.tb:hover, .tb.hi {{
  overflow: visible;
  background: rgba(255,255,180,.75);
  border-color: currentColor;
  z-index: 5;
}}
.tb p {{
  margin: 0; line-height: 1.2;
  overflow: hidden; height: 100%;
  white-space: normal; word-break: break-word;
}}
.tb.title p {{ font-weight: bold; }}

/* ── Tooltip bas de page ──────────────────────────── */
#tip {{
  position: fixed; bottom: 0; left: 0; right: 0;
  background: rgba(0,0,0,.9); border-top: 1px solid #555;
  padding: 6px 14px; font-size: 11px; max-height: 130px;
  overflow-y: auto; display: none;
  white-space: pre-wrap; word-break: break-word; z-index: 20;
}}
</style>
</head>
<body>

<div id="header">
  <b>PaddleOCR — {ARK}</b>
  <span>{len(blocks)} blocs · {n_cols} col · {img_w}×{img_h}px</span>
  <span>Cliquez un bloc pour le synchroniser</span>
</div>

<div id="panels">

  <!-- ── Gauche : image + overlay ── -->
  <div id="left">
    <div id="img-wrap">
      <img id="img-page" src="data:image/jpeg;base64,{img_b64}">
    </div>
  </div>

  <!-- ── Droite : texte positionné ── -->
  <div id="right">
    <div id="page-right"></div>
  </div>

</div>

<div id="tip"></div>

<script>
const blocks   = {blocks_js};
const COLORS   = {col_colors};
const n_cols   = {n_cols};
const tip      = document.getElementById("tip");
let   activeIdx = null;

/* ── Construction overlay gauche ──────────────────── */
function buildLeft() {{
  const wrap = document.getElementById("img-wrap");
  blocks.forEach((b, i) => {{
    const d = document.createElement("div");
    d.className = "box";
    d.dataset.i = i;
    d.style.left   = (b.x0 * 100) + "%";
    d.style.top    = (b.y0 * 100) + "%";
    d.style.width  = ((b.x1 - b.x0) * 100) + "%";
    d.style.height = ((b.y1 - b.y0) * 100) + "%";
    const c = COLORS[(b.col ?? 0) % COLORS.length];
    d.style.borderColor = c;
    d.style.background  = c + "44";
    d.addEventListener("click", () => activate(i));
    wrap.appendChild(d);
  }});
}}

/* ── Construction panneau droit ───────────────────── */
function buildRight() {{
  const page = document.getElementById("page-right");
  blocks.forEach((b, i) => {{
    const d = document.createElement("div");
    d.className = "tb" + (b.label === "Title" ? " title" : "");
    d.dataset.i = i;
    d.style.left   = (b.x0 * 100) + "%";
    d.style.top    = (b.y0 * 100) + "%";
    d.style.width  = ((b.x1 - b.x0) * 100) + "%";
    d.style.height = ((b.y1 - b.y0) * 100) + "%";
    const c = COLORS[(b.col ?? 0) % COLORS.length];
    d.style.color = c;

    const p = document.createElement("p");
    p.textContent = b.text ?? "";
    d.appendChild(p);
    d.addEventListener("click", () => activate(i));
    page.appendChild(d);
  }});
  fitFonts();
}}

/* ── Taille de fonte adaptée à chaque bloc ────────── */
function fitFonts() {{
  const page = document.getElementById("page-right");
  const PH   = page.getBoundingClientRect().height;
  const PW   = page.getBoundingClientRect().width;
  document.querySelectorAll(".tb p").forEach((p, i) => {{
    const b = blocks[i];
    const bh = (b.y1 - b.y0) * PH;
    const bw = (b.x1 - b.x0) * PW;
    const chars = (b.text ?? "").length;
    const charsPerLine = Math.max(1, bw / 5.5);
    const lines = Math.max(1, Math.ceil(chars / charsPerLine));
    const fs = Math.min(11, Math.max(4.5, (bh / lines) / 1.3));
    p.style.fontSize = fs + "px";
  }});
}}

/* ── Activation / synchronisation ────────────────── */
function activate(idx) {{
  if (activeIdx !== null) {{
    document.querySelectorAll(`[data-i="${{activeIdx}}"]`).forEach(el => el.classList.remove("hi"));
  }}
  activeIdx = idx;
  document.querySelectorAll(`[data-i="${{idx}}"]`).forEach(el => el.classList.add("hi"));

  const b = blocks[idx];
  tip.style.display = "block";
  tip.textContent =
    `[${{b.position ?? idx}}] ${{b.label}}  col${{(b.col??0)+1}}  conf=${{(b.confidence??0).toFixed(3)}}  y=${{b.y0.toFixed(3)}}–${{b.y1.toFixed(3)}}\n${{b.text ?? ""}}`;

  /* Scroll centré dans chaque panneau */
  const leftEl  = document.getElementById("left");
  const rightEl = document.getElementById("right");
  const imgH    = document.getElementById("img-wrap").offsetHeight;
  const pageH   = document.getElementById("page-right").offsetHeight;

  leftEl.scrollTop  = b.y0 * imgH  - leftEl.clientHeight  * .35;
  rightEl.scrollTop = b.y0 * pageH - rightEl.clientHeight * .35;
}}

/* ── Recalcul fontes au resize ────────────────────── */
new ResizeObserver(fitFonts).observe(document.getElementById("page-right"));

/* ── Init ─────────────────────────────────────────── */
const img = document.getElementById("img-page");
function init() {{ buildLeft(); buildRight(); }}
img.complete ? init() : (img.onload = init);
</script>
</body>
</html>"""

out_path.write_text(html, encoding="utf-8")
print(f"OK → {out_path}")
