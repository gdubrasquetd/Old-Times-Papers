"""
Pipeline complet : PaddleOCR (détection lignes) + Kraken CATMuS (reconnaissance)
Génère _layout_kraken.json + _viz_kraken.html

Usage : python OCR/verify_kraken.py [ARK]
"""
import json, pathlib, sys, base64, time
from collections import defaultdict
from PIL import Image

ROOT       = pathlib.Path(__file__).parent.parent
ARK        = sys.argv[1] if len(sys.argv) > 1 else "bpt6k412758h"
IMG_PATH   = ROOT / "cache" / "ocr_img" / f"{ARK}.jpg"
OUT_DIR    = ROOT / "cache" / "verify"
MODEL_PATH = pathlib.Path(
    r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
    r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel"
)

OUT_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))

if not IMG_PATH.exists():
    print("Image manquante — lance verify_paddle.py d'abord"); sys.exit(1)

img = Image.open(IMG_PATH).convert("RGB")
img_w, img_h = img.size
print(f"Image : {img_w}×{img_h}")

# ── 1. Détection de lignes via PaddleOCR ──────────────────────────────────────
print("PaddleOCR : détection des lignes…")
from OCR.ocr_local import _run_paddle_worker, _find_col_boundaries, HAS_NUMPY
import numpy as _np

t0 = time.time()
worker = _run_paddle_worker(IMG_PATH)
if "error" in worker:
    print(f"Erreur PaddleOCR : {worker['error']}"); sys.exit(1)
raw_lines = worker["lines"]
print(f"  {len(raw_lines)} lignes en {time.time()-t0:.1f}s")

# ── 2. Chargement Kraken ──────────────────────────────────────────────────────
print("Chargement Kraken CATMuS-Print…")
from kraken.lib import models as kraken_models
from kraken import rpred as kraken_rpred
from kraken.containers import Segmentation, BBoxLine

t1 = time.time()
net = kraken_models.load_any(str(MODEL_PATH))
print(f"  modèle chargé en {time.time()-t1:.1f}s")

# ── 3. Reconnaissance Kraken ligne par ligne ──────────────────────────────────
print(f"Reconnaissance Kraken sur {len(raw_lines)} lignes…")
t2 = time.time()

recognized = []
margin = 3   # px de marge autour de chaque ligne

for i, line in enumerate(raw_lines):
    x0 = max(0, int(line["x0"]) - margin)
    y0 = max(0, int(line["y0"]) - margin)
    x1 = min(img_w, int(line["x1"]) + margin)
    y1 = min(img_h, int(line["y1"]) + margin)

    crop = img.crop((x0, y0, x1, y1))
    cw, ch = crop.size
    if cw < 10 or ch < 4:
        recognized.append({"text": line["text"], "conf": line["conf"], **{k: line[k] for k in ("x0","y0","x1","y1","w","h","xc","col") if k in line}})
        continue

    # Upscale si la ligne est petite
    scale = max(1, min(4, 80 // max(ch, 1)))
    if scale > 1:
        crop = crop.resize((cw * scale, ch * scale), Image.LANCZOS)

    # Segmentation BBox simple : une seule ligne = tout le crop
    line_box = BBoxLine(
        id="l0",
        bbox=(0, 0, crop.width, crop.height),
        text=None,
    )
    seg = Segmentation(
        type="bbox",
        imagename="",
        text_direction="horizontal-lr",
        script_detection=False,
        lines=[line_box],
        regions={},
        line_orders=[],
    )

    text = line["text"]   # fallback PaddleOCR
    try:
        for record in kraken_rpred.rpred(net, crop, seg):
            if record.prediction.strip():
                text = record.prediction
            break
    except Exception:
        pass

    recognized.append({
        "text":  text,
        "conf":  line["conf"],
        "x0": line["x0"], "y0": line["y0"],
        "x1": line["x1"], "y1": line["y1"],
        "w":  line["w"],  "h":  line["h"],
        "xc": line["xc"],
    })

    if (i + 1) % 50 == 0:
        elapsed = time.time() - t2
        print(f"  {i+1}/{len(raw_lines)} lignes — {elapsed:.0f}s")

print(f"  terminé en {time.time()-t2:.1f}s")

# ── 4. Groupement en blocs (même logique que ocr_local.py) ───────────────────
print("Groupement en blocs…")
LABEL_COLORS = {"Title": "#8b2c2c", "Text": "#1a4a7a"}

max_line_w = img_w // 5
narrow_xcs = [l["xc"] for l in recognized if l["w"] < max_line_w]
col_bounds = _find_col_boundaries(narrow_xcs, img_w)
n_cols = len(col_bounds) - 1

def col_of(xc):
    for j in range(n_cols):
        if col_bounds[j] <= xc < col_bounds[j + 1]:
            return j
    return n_cols - 1

for l in recognized:
    l["col"] = col_of(l["xc"])

blocks = []
for col_i in range(n_cols):
    col_lines = sorted([l for l in recognized if l["col"] == col_i], key=lambda l: (l["y0"], l["x0"]))
    if not col_lines: continue
    hs = sorted(l["h"] for l in col_lines if 3 < l["h"] < 200)
    med_h = hs[len(hs) // 2] if hs else 14
    gap_thresh = max(med_h * 0.8, 6)
    cx0, cx1 = col_bounds[col_i], col_bounds[col_i + 1]

    groups = [[col_lines[0]]]
    for l in col_lines[1:]:
        if l["y0"] - groups[-1][-1]["y1"] > gap_thresh:
            groups.append([l])
        else:
            groups[-1].append(l)

    for grp in groups:
        text = " ".join(l["text"] for l in grp)
        if len(text.replace(" ", "")) < 4: continue
        by0 = min(l["y0"] for l in grp)
        by1 = max(l["y1"] for l in grp)
        if len(grp) == 1 and (by1 - by0) / img_h < 0.015: continue
        g_hs = [l["h"] for l in grp if 3 < l["h"] < 200]
        avg_h = sum(g_hs) / len(g_hs) if g_hs else 0
        label = "Title" if avg_h > med_h * 1.4 else "Text"
        blocks.append({
            "label": label,
            "x0": round(cx0 / img_w, 4), "y0": round(by0 / img_h, 4),
            "x1": round(cx1 / img_w, 4), "y1": round(by1 / img_h, 4),
            "position": len(blocks),
            "confidence": round(sum(l["conf"] for l in grp) / len(grp), 3),
            "color": LABEL_COLORS.get(label, "#888"),
            "text": text,
            "col": col_i,
        })

blocks.sort(key=lambda b: (b["col"], round(b["y0"] * 20)))
for i, b in enumerate(blocks): b["position"] = i
print(f"  {len(blocks)} blocs (Kraken)")

# ── 5. Sauvegarde JSON ────────────────────────────────────────────────────────
layout = {"blocks": blocks, "img_w": img_w, "img_h": img_h,
          "n_cols": n_cols, "engine": "kraken"}
json_path = OUT_DIR / f"{ARK}_layout_kraken.json"
json_path.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
print(f"JSON → {json_path.name}")

# ── 6. HTML de visualisation ──────────────────────────────────────────────────
print("Génération HTML…")
img_b64    = base64.b64encode(IMG_PATH.read_bytes()).decode()
blocks_js  = json.dumps(blocks, ensure_ascii=False)
col_colors = json.dumps(["#c0392b","#d35400","#b8860b","#27ae60",
                          "#16a085","#2471a3","#7d3c98","#c0392b"])
ratio = f"{img_w}/{img_h}"

html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Kraken — {ARK}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;background:#111;color:#eee;font-family:monospace;overflow:hidden}}
#header{{height:34px;padding:0 14px;display:flex;align-items:center;gap:20px;
  background:#1c1c2e;border-bottom:1px solid #333;font-size:12px}}
#header b{{font-size:13px}}
#header span{{color:#999}}
#panels{{display:flex;height:calc(100vh - 34px)}}
#left{{flex:1;overflow-y:auto;overflow-x:hidden;background:#1a1a2e;border-right:2px solid #444}}
#img-wrap{{position:relative;width:100%}}
#img-wrap img{{display:block;width:100%;height:auto}}
.box{{position:absolute;border:2px solid;opacity:.35;cursor:pointer;transition:opacity .12s}}
.box:hover,.box.hi{{opacity:.85;z-index:5}}
#right{{flex:1;overflow-y:auto;overflow-x:hidden;background:#f5f0e8}}
#page-right{{position:relative;width:100%;aspect-ratio:{ratio};background:#fdf8f0}}
.tb{{position:absolute;overflow:hidden;cursor:pointer;border:1.5px solid transparent;padding:1px 2px}}
.tb:hover,.tb.hi{{overflow:visible;background:rgba(255,255,180,.75);border-color:currentColor;z-index:5}}
.tb p{{margin:0;line-height:1.2;overflow:hidden;height:100%;white-space:normal;word-break:break-word}}
.tb.title p{{font-weight:bold}}
#tip{{position:fixed;bottom:0;left:0;right:0;background:rgba(0,0,0,.9);border-top:1px solid #555;
  padding:6px 14px;font-size:11px;max-height:130px;overflow-y:auto;display:none;
  white-space:pre-wrap;word-break:break-word;z-index:20}}
</style></head><body>
<div id="header">
  <b>Kraken CATMuS-Print — {ARK}</b>
  <span>{len(blocks)} blocs · {n_cols} col · {img_w}×{img_h}px</span>
  <span>Cliquez un bloc pour synchroniser</span>
</div>
<div id="panels">
  <div id="left"><div id="img-wrap"><img id="img-page" src="data:image/jpeg;base64,{img_b64}"></div></div>
  <div id="right"><div id="page-right"></div></div>
</div>
<div id="tip"></div>
<script>
const blocks={blocks_js};
const COLORS={col_colors};
const tip=document.getElementById("tip");
let active=null;

function buildLeft(){{
  const wrap=document.getElementById("img-wrap");
  blocks.forEach((b,i)=>{{
    const d=document.createElement("div");
    d.className="box";d.dataset.i=i;
    d.style.left=(b.x0*100)+"%";d.style.top=(b.y0*100)+"%";
    d.style.width=((b.x1-b.x0)*100)+"%";d.style.height=((b.y1-b.y0)*100)+"%";
    const c=COLORS[(b.col??0)%COLORS.length];
    d.style.borderColor=c;d.style.background=c+"44";
    d.addEventListener("click",()=>activate(i));wrap.appendChild(d);
  }});
}}

function buildRight(){{
  const page=document.getElementById("page-right");
  blocks.forEach((b,i)=>{{
    const d=document.createElement("div");
    d.className="tb"+(b.label==="Title"?" title":"");d.dataset.i=i;
    d.style.left=(b.x0*100)+"%";d.style.top=(b.y0*100)+"%";
    d.style.width=((b.x1-b.x0)*100)+"%";d.style.height=((b.y1-b.y0)*100)+"%";
    d.style.color=COLORS[(b.col??0)%COLORS.length];
    const p=document.createElement("p");p.textContent=b.text??"";
    d.appendChild(p);d.addEventListener("click",()=>activate(i));page.appendChild(d);
  }});
  fitFonts();
}}

function fitFonts(){{
  const page=document.getElementById("page-right");
  const r=page.getBoundingClientRect();
  document.querySelectorAll(".tb p").forEach((p,i)=>{{
    const b=blocks[i];
    const bh=(b.y1-b.y0)*r.height,bw=(b.x1-b.x0)*r.width;
    const chars=(b.text??"").length;
    const cpl=Math.max(1,bw/5.5);
    const lines=Math.max(1,Math.ceil(chars/cpl));
    p.style.fontSize=Math.min(11,Math.max(4.5,(bh/lines)/1.3))+"px";
  }});
}}

function activate(idx){{
  if(active!==null)document.querySelectorAll(`[data-i="${{active}}"]`).forEach(e=>e.classList.remove("hi"));
  active=idx;
  document.querySelectorAll(`[data-i="${{idx}}"]`).forEach(e=>e.classList.add("hi"));
  const b=blocks[idx];
  tip.style.display="block";
  tip.textContent=`[${{b.position??idx}}] ${{b.label}}  col${{(b.col??0)+1}}  conf=${{(b.confidence??0).toFixed(3)}}  y=${{b.y0.toFixed(3)}}–${{b.y1.toFixed(3)}}\n${{b.text??""}}`;
  const L=document.getElementById("left"),R=document.getElementById("right");
  const iH=document.getElementById("img-wrap").offsetHeight;
  const pH=document.getElementById("page-right").offsetHeight;
  L.scrollTop=b.y0*iH-L.clientHeight*.35;
  R.scrollTop=b.y0*pH-R.clientHeight*.35;
}}

new ResizeObserver(fitFonts).observe(document.getElementById("page-right"));
const img=document.getElementById("img-page");
function init(){{buildLeft();buildRight();}}
img.complete?init():(img.onload=init);
</script></body></html>"""

html_path = OUT_DIR / f"{ARK}_viz_kraken.html"
html_path.write_text(html, encoding="utf-8")
print(f"HTML → {html_path.name}")
print(f"\nTerminé. Ouverture…")
