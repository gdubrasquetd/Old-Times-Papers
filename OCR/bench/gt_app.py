"""
Soft de création de VÉRITÉ TERRAIN (GT) — mode overlay AU MOT.

Tesseract fournit les mots AVEC leurs boîtes ; on pose chaque mot deviné
exactement sur sa position dans l'image (transparent, éditable). Tu corriges les
mots faux là où ils sont. La correction (positions + textes) est persistée dans
gt_words.json ; le texte reconstruit (ordre de lecture) est la GT (gt.json), qui
sert à mesurer le CER des moteurs.

Env : bloc_detection (flask). Tesseract via le binaire (pas de dépendance python).
    python gt_app.py   ->   http://localhost:5056
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, difflib
from pathlib import Path
from flask import Flask, request, jsonify, send_file, Response

HERE = Path(__file__).resolve().parent
CROPS = HERE / "crops"
MANIFEST = HERE / "manifest.json"
GT_PATH = HERE / "gt.json"
WORDS_PATH = HERE / "gt_words.json"
PRE_DIR = HERE / "_pre"          # cache des crops prétraités (binarisés)
PRE_DIR.mkdir(exist_ok=True)
TESS = shutil.which("tesseract") or r"C:/Program Files/Tesseract-OCR/tesseract.exe"

# Filtrage de la sortie Tesseract : jette le bruit à basse confiance.
MIN_CONF = 30            # en dessous : token quasi certainement du bruit
MIN_CONF_SHORT = 55      # mono/bi-caractères non alphanumériques : seuil plus strict
DESPECKLE_MIN_AREA = 15  # despeckle CC : retire les composantes < N px (sans manger les accents)

app = Flask(__name__)


def load_json(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def preprocess_for_ocr(src: Path) -> Path:
    """Binarise le crop pour un OCR plus propre, EN GARDANT les dimensions
    (les boîtes restent valables sur l'image d'origine). Stratégie adaptée à
    l'imprimé ancien : despeckle léger -> seuil adaptatif (éclairage inégal) ->
    retrait des micro-taches par composantes connexes (épargne les accents).
    Renvoie le chemin du crop prétraité (mis en cache dans _pre/)."""
    try:
        import cv2, numpy as np
    except Exception:
        return src
    dst = PRE_DIR / (src.stem + "_pre.png")
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    img = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return src
    img = cv2.medianBlur(img, 3)                      # despeckle doux
    bw = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 31, 15)   # texte noir / fond blanc
    inv = 255 - bw                                    # texte en blanc pour l'analyse CC
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < DESPECKLE_MIN_AREA:   # micro-bruit (pas les accents)
            inv[labels == i] = 0
    bw = 255 - inv
    cv2.imwrite(str(dst), bw)
    return dst


def tesseract_words(p: Path, preprocess: bool = True) -> list[dict]:
    """Mots + boîtes via Tesseract (TSV), dans l'ordre de lecture. Prétraite le
    crop (binarisation) et filtre les tokens à basse confiance."""
    img = preprocess_for_ocr(p) if preprocess else p
    try:
        out = subprocess.run([TESS, str(img), "stdout", "-l", "fra", "--psm", "6", "tsv"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    except Exception:
        return []
    ws = []
    for ln in out.stdout.splitlines()[1:]:
        c = ln.split("\t")
        if len(c) < 12 or c[0] != "5":
            continue
        txt = c[11].strip()
        if not txt:
            continue
        try:
            conf = float(c[10])
        except ValueError:
            conf = -1
        # filtrage du bruit : basse confiance, ou petit token non alphanumérique peu sûr
        if conf < MIN_CONF:
            continue
        if len(txt) <= 2 and not any(ch.isalnum() for ch in txt) and conf < MIN_CONF_SHORT:
            continue
        left, top, w, h = (int(c[i]) for i in (6, 7, 8, 9))
        ws.append({"text": txt, "x0": left, "y0": top, "x1": left + w, "y1": top + h,
                   "b": int(c[2]), "p": int(c[3]), "l": int(c[4]), "w": int(c[5])})
    return ws


def align_gt_to_boxes(tess: list[dict], gt_text: str) -> list[dict]:
    """Pose MES mots (gt_text) sur les boîtes de Tesseract via un alignement de
    séquences. Les tokens Tesseract seuls (bruit) sont abandonnés ; mes mots que
    Tesseract a ratés sont placés juste après le mot précédent, sur la même ligne."""
    gt_tokens = [t for t in re.split(r"\s+", gt_text.strip()) if t]
    if not gt_tokens:
        return tess
    a = [t["text"].lower() for t in tess]
    b = [g.lower() for g in gt_tokens]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[dict] = []

    def at_box(word, t):
        out.append({"text": word, "x0": t["x0"], "y0": t["y0"], "x1": t["x1"], "y1": t["y1"],
                    "b": t["b"], "p": t["p"], "l": t["l"], "w": t["w"]})

    def after_last(word):
        if not out:
            out.append({"text": word, "x0": 0, "y0": 0, "x1": 40, "y1": 22,
                        "b": 0, "p": 0, "l": 0, "w": len(out)})
            return
        L = out[-1]
        h = max(8, L["y1"] - L["y0"])
        x0 = L["x1"] + int(h * 0.3)
        out.append({"text": word, "x0": x0, "y0": L["y0"], "x1": x0 + int(len(word) * h * 0.5),
                    "y1": L["y1"], "b": L["b"], "p": L["p"], "l": L["l"], "w": L["w"] + 1})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                at_box(gt_tokens[j1 + k], tess[i1 + k])
        elif tag == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                gj = j1 + k
                if gj >= j2:
                    continue                       # boîte Tesseract en trop -> drop
                if i1 + k < i2:
                    at_box(gt_tokens[gj], tess[i1 + k])
                else:
                    after_last(gt_tokens[gj])
        elif tag == "insert":
            for k in range(j1, j2):
                after_last(gt_tokens[k])
        # 'delete' : tokens Tesseract seuls (bruit) -> ignorés
    return out


PAGE = r"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8>
<title>GT au mot — banc OCR</title><style>
body{font-family:sans-serif;background:#1e1e1e;color:#ddd;margin:0;display:flex;height:100vh}
#left{flex:1.7;display:flex;flex-direction:column;border-right:2px solid #111;min-width:0}
#imgwrap{flex:1;overflow:auto;background:#333;padding:10px;display:flex;justify-content:center;align-items:flex-start}
#imgstack{position:relative}
#imgstack img{display:block;max-width:100%;background:#fff}
#words{position:absolute;top:0;left:0;width:100%;height:100%}
.word{position:absolute;white-space:nowrap;line-height:1;color:#e01020;font-weight:600;
  background:rgba(255,255,255,.25);outline:none;padding:0 1px;border-radius:2px;
  font-family:Georgia,'Times New Roman',serif;cursor:text;
  text-shadow:-1px -1px 0 #000,1px -1px 0 #000,-1px 1px 0 #000,1px 1px 0 #000,
    0 -1px 0 #000,0 1px 0 #000,-1px 0 0 #000,1px 0 0 #000}
.word:focus{background:#ff6;color:#000;z-index:5}
#left textarea{height:220px;margin:0 8px 8px;background:#111;color:#eee;border:1px solid #444;
  border-radius:4px;padding:8px;font-family:Consolas,monospace;font-size:14px;line-height:1.45;resize:vertical}
#right{flex:0 0 300px;display:flex;flex-direction:column;padding:12px;gap:9px;overflow:auto}
h2{margin:0;font-size:15px} .meta{color:#999;font-size:12px}
.ctl{display:flex;align-items:center;gap:8px;font-size:12px;color:#bbb}
.ctl input[type=range]{flex:1}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{padding:9px 13px;border:0;border-radius:5px;cursor:pointer;font-size:14px;color:#fff}
.ok{background:#2a9d8f} .nav{background:#555} .clear{background:#7a5} .reocr{background:#c0392b}
#status{color:#8bd;font-size:13px;min-height:18px}
.progress{color:#9c9;font-size:13px} .hint{color:#888;font-size:11px}
</style></head><body>
<div id=left>
  <div id=imgwrap>
    <div id=imgstack>
      <img id=crop>
      <div id=words></div>
    </div>
  </div>
  <textarea id=txt spellcheck=false placeholder="texte reconstruit (éditable) — c'est lui qui est enregistré comme GT"></textarea>
</div>
<div id=right>
  <div class=bar><h2 id=title>—</h2><span class=progress id=prog></span></div>
  <select id=picker style="width:100%;background:#111;color:#eee;border:1px solid #444;padding:6px;border-radius:4px"></select>
  <div class=meta id=meta></div>
  <p class=hint>Chaque mot deviné par Tesseract est posé sur sa position. Clique un mot faux et corrige-le sur place. Puis 💾.</p>
  <fieldset style="border:1px solid #444;border-radius:5px;padding:6px 8px">
    <legend style="font-size:11px;color:#888">calque</legend>
    <div class=ctl>taille %<input id=cScale type=range min=0.5 max=1.6 step=0.05 value=1></div>
    <div class=ctl>opacité fond<input id=cBg type=range min=0 max=0.9 step=0.05 value=0.25></div>
    <div class=ctl>couleur<input id=cCol type=color value="#d00000"> <label style="margin-left:auto"><input type=checkbox id=cHide> masquer</label></div>
  </fieldset>
  <div class=bar>
    <button class=reocr id=btnreocr>↻ ré-OCR (Tesseract)</button>
    <label class=ctl style="gap:4px"><input type=checkbox id=cRaw> sans prétrait.</label>
    <label class=ctl style="gap:4px"><input type=checkbox id=cPre> voir binarisé</label>
  </div>
  <div id=status></div>
  <div class=bar>
    <button class=nav id=btnprev>← Précédent</button>
    <button class=ok id=btnsave>💾 Enregistrer &amp; suivant →</button>
    <button class=nav id=btnnext>Passer →</button>
  </div>
</div>
<script>
let items=[], idx=0;
const $=id=>document.getElementById(id);

function fontScale(){ return parseFloat($('cScale').value); }
// reconstruit le texte (ordre de lecture) à partir des mots du calque
function reconstruct(){
  const words=[...$('words').querySelectorAll('.word')].map(s=>({t:s.textContent.trim(),
    b:+s.dataset.b,p:+s.dataset.p,l:+s.dataset.l,w:+s.dataset.w})).filter(x=>x.t);
  words.sort((a,b)=>a.b-b.b||a.p-b.p||a.l-b.l||a.w-b.w);
  let text='',pl=null;
  for(const wd of words){ const key=wd.b+'/'+wd.p+'/'+wd.l;
    text += pl===null?wd.t:(key!==pl?'\n'+wd.t:' '+wd.t); pl=key; }
  return text;
}
function syncText(){ $('txt').value=reconstruct(); }
function applyStyle(){
  document.querySelectorAll('.word').forEach(s=>{
    s.style.color=$('cCol').value;
    s.style.background='rgba(255,255,255,'+$('cBg').value+')';
    s.style.fontSize=(parseFloat(s.dataset.fs)*fontScale())+'px';
  });
  $('words').style.display=$('cHide').checked?'none':'block';
  localStorage.setItem('gtstyle2',JSON.stringify({sc:$('cScale').value,b:$('cBg').value,c:$('cCol').value,h:$('cHide').checked}));
}
function restoreStyle(){
  try{const v=JSON.parse(localStorage.getItem('gtstyle2'));
    if(v){$('cScale').value=v.sc;$('cBg').value=v.b;$('cCol').value=v.c;$('cHide').checked=!!v.h;}}catch(e){}
}
['cScale','cBg','cCol','cHide'].forEach(id=>$(id).addEventListener('input',applyStyle));

function optLabel(it,i){ return `${i+1}. ${it.class} ${it.gt?'✓ (à relire)':'· vide'}`; }
function populate(){
  const s=$('picker'); s.innerHTML='';
  items.forEach((it,i)=>{ const o=document.createElement('option'); o.value=i; o.textContent=optLabel(it,i); s.appendChild(o); });
}
async function load(){
  items=await fetch('/api/items').then(r=>r.json());
  populate(); restoreStyle();
  idx=items.findIndex(it=>!it.gt); if(idx<0)idx=0;   // reprend au 1er bloc NON fait
  show();
}
function show(){
  const it=items[idx];
  $('title').textContent=it.class;
  $('meta').textContent=it.image+' — '+it.file;
  $('prog').textContent=(idx+1)+'/'+items.length+' · '+items.filter(x=>x.gt).length+' faits';
  $('picker').value=idx; $('status').textContent='';
  $('words').innerHTML='';
  $('crop').onload=()=>renderWords(it.file,false);
  $('crop').src=cropSrc(it.file);
}
function cropSrc(file){ return ($('cPre').checked?'/pre/':'/crops/')+encodeURIComponent(file); }
async function renderWords(file,fresh){
  const raw=$('cRaw').checked?'&raw=1':'';
  const data=await fetch('/api/words?file='+encodeURIComponent(file)+(fresh?'&fresh=1':'')+raw).then(r=>r.json());
  const scale=$('crop').clientWidth/($('crop').naturalWidth||1);
  const w=$('words'); w.innerHTML='';
  data.words.forEach(wd=>{
    const s=document.createElement('span');
    s.className='word'; s.contentEditable='true'; s.spellcheck=false; s.textContent=wd.text;
    const fs=(wd.y1-wd.y0)*scale*0.9;
    Object.assign(s.dataset,{x0:wd.x0,y0:wd.y0,x1:wd.x1,y1:wd.y1,b:wd.b,p:wd.p,l:wd.l,w:wd.w,fs:fs});
    s.style.left=(wd.x0*scale)+'px'; s.style.top=(wd.y0*scale)+'px';
    s.addEventListener('input',syncText);     // corriger un mot -> maj du texte en bas
    w.appendChild(s);
  });
  applyStyle();
  syncText();
  $('status').textContent = ({saved:'corrections sauvegardées chargées',
    'gt-aligned':'ma transcription alignée sur les boîtes',tesseract:'Tesseract brut (pas de GT)'}[data.source]||'')
    +' — '+data.words.length+' mots';
}
function nextEmpty(){ for(let k=1;k<=items.length;k++){const j=(idx+k)%items.length; if(!items[j].gt)return j;} return (idx+1)%items.length; }
$('picker').onchange=()=>{idx=+$('picker').value;show()};
$('btnprev').onclick=()=>{idx=(idx-1+items.length)%items.length;show()};
$('btnnext').onclick=()=>{idx=(idx+1)%items.length;show()};
$('btnreocr').onclick=()=>{ if(confirm('Refaire l\'OCR Tesseract ? (perd les corrections de ce bloc)')) renderWords(items[idx].file,true); };
$('cRaw').addEventListener('change',()=>renderWords(items[idx].file,true));   // A/B prétraitement
$('cPre').addEventListener('change',()=>{ $('crop').src=cropSrc(items[idx].file); });   // voir l'image binarisée

async function save(go){
  const it=items[idx];
  const text=$('txt').value;                 // le texte du bas = GT autoritaire
  const words=[...$('words').querySelectorAll('.word')].map(s=>({text:s.textContent.trim(),
    x0:+s.dataset.x0,y0:+s.dataset.y0,x1:+s.dataset.x1,y1:+s.dataset.y1,
    b:+s.dataset.b,p:+s.dataset.p,l:+s.dataset.l,w:+s.dataset.w})).filter(x=>x.text);
  await fetch('/api/gt',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({file:it.file,text,words})});
  it.gt=text; $('picker').options[idx].textContent=optLabel(it,idx);
  $('status').textContent='enregistré';
  if(go){ idx=nextEmpty(); show(); }   // saute vers le prochain bloc NON fait
}
$('btnsave').onclick=()=>save(true);
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();save(true);}
});
window.addEventListener('resize',()=>renderWords(items[idx].file,false));
load();
</script></body></html>"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/crops/<path:name>")
def crop(name):
    p = CROPS / name
    return send_file(str(p), mimetype="image/png") if p.exists() else ("", 404)


@app.route("/pre/<path:name>")
def pre(name):
    """Image prétraitée (binarisée) — mêmes dimensions que l'original."""
    src = CROPS / name
    if not src.exists():
        return ("", 404)
    return send_file(str(preprocess_for_ocr(src)), mimetype="image/png")


@app.route("/api/items")
def items():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gt = load_json(GT_PATH)
    return jsonify([{"file": m["file"], "class": m["class"], "image": m["image"],
                     "gt": gt.get(m["file"], "")} for m in manifest])


@app.route("/api/words")
def words():
    """Mots + boîtes. Priorité : layout sauvegardé > MA transcription (gt.json)
    alignée sur les boîtes Tesseract > Tesseract brut. `fresh=1` force Tesseract brut."""
    file = request.args.get("file", "")
    fresh = request.args.get("fresh")
    raw = request.args.get("raw")            # raw=1 -> Tesseract sans prétraitement
    layouts = load_json(WORDS_PATH)
    if not fresh and file in layouts:
        return jsonify({"words": layouts[file], "source": "saved"})
    p = CROPS / file
    if not p.exists():
        return jsonify({"words": [], "source": "none"})
    tess = tesseract_words(p, preprocess=not raw)
    gt = load_json(GT_PATH).get(file, "")
    if gt.strip() and not fresh:
        return jsonify({"words": align_gt_to_boxes(tess, gt), "source": "gt-aligned"})
    return jsonify({"words": tess, "source": "tesseract"})


@app.route("/api/gt", methods=["POST"])
def save_gt():
    d = request.get_json()
    gt = load_json(GT_PATH)
    gt[d["file"]] = d["text"]
    GT_PATH.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    if "words" in d:
        wl = load_json(WORDS_PATH)
        wl[d["file"]] = d["words"]
        WORDS_PATH.write_text(json.dumps(wl, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("GT au mot : http://localhost:5056", flush=True)
    app.run(host="127.0.0.1", port=5056, threaded=True)
