"""Étape 3 : construit le JUMEAU NUMÉRIQUE d'une une à partir de blocks.json (avec texte).
Trois zones : à gauche le scan avec les blocs, à droite la page reconstruite (texte OCR
aux mêmes positions), et EN BAS un panneau de contrôle qui, au clic sur un bloc, montre
l'image lisible du bloc + sa transcription OCR côte à côte.
Env : n'importe lequel avec PIL. python twin_build.py <blocks.json> <out.html>
"""
import base64, io, json, re, sys
from pathlib import Path
from PIL import Image

PAPERS = {
    "le_figaro": "Le Figaro", "le_temps": "Le Temps", "le_matin": "Le Matin",
    "le_journal": "Le Journal", "le_gaulois": "Le Gaulois", "petit_journal": "Le Petit Journal",
    "petit_parisien": "Le Petit Parisien", "intransigeant": "L'Intransigeant",
    "humanite": "L'Humanité", "action_francaise": "L'Action Française",
    "journal_des_debats": "Journal des Débats", "echo_de_paris": "L'Écho de Paris",
    "excelsior": "Excelsior", "la_croix": "La Croix", "oeuvre": "L'Œuvre",
    "populaire": "Le Populaire",
}
COLORS = {"header": "#8b2c2c", "titre": "#c0392b", "bloc de texte": "#1a4a7a",
          "texte isolé": "#7d3c98", "illustration": "#5a6b3a", "autres": "#777"}


def paper_name(slug):
    key = re.sub(r"_\d{4}-\d{2}-\d{2}$", "", slug)
    return PAPERS.get(key, key.replace("_", " ").title())


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def crop_b64(im, box, maxw=680):
    x0, y0, x1, y1 = box
    c = im.crop((x0, y0, x1, y1))
    if c.width > maxw:
        c = c.resize((maxw, max(1, round(c.height * maxw / c.width))), Image.LANCZOS)
    buf = io.BytesIO(); c.save(buf, "JPEG", quality=78)
    return base64.b64encode(buf.getvalue()).decode()


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a["box"]; bx0, by0, bx1, by1 = b["box"]
    inter = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    if not inter:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua else 0.0


def dedup_blocks(blocks, iou_thresh=0.6):
    """Retire les blocs quasi superposés (IoU > seuil) portant le MÊME texte ;
    garde celui de meilleure confiance. Renvoie (blocs_gardés, nb_retirés)."""
    def _nt(s):
        return re.sub(r"\s+", "", (s or "").lower())
    drop = set()
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            if i in drop or j in drop:
                continue
            ti, tj = _nt(blocks[i].get("text")), _nt(blocks[j].get("text"))
            if ti and ti == tj and _iou(blocks[i], blocks[j]) > iou_thresh:
                drop.add(j if blocks[i].get("conf", 0) >= blocks[j].get("conf", 0) else i)
    return [b for k, b in enumerate(blocks) if k not in drop], len(drop)


def main():
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    im = Image.open(data["image"]).convert("RGB")
    W, H = data["img_w"], data["img_h"]

    # dédup : blocs quasi superposés portant le même texte -> on n'en garde qu'un
    data["blocks"], n_drop = dedup_blocks(data["blocks"])
    if n_drop:
        print(f"  dédup : {n_drop} blocs superposés retirés", flush=True)

    disp = im.copy()
    if disp.width > 1500:
        disp = disp.resize((1500, round(1500 * H / W)), Image.LANCZOS)
    buf = io.BytesIO(); disp.save(buf, "JPEG", quality=82)
    b64 = base64.b64encode(buf.getvalue()).decode()

    name = paper_name(data["slug"])
    for b in data["blocks"]:
        if b["class"] == "header":
            b["display"] = name; b["ocr"] = name + "  (en-tête — nom du journal, non OCRisé)"
        elif b["class"] == "illustration":
            b["display"] = None; b["ocr"] = "(illustration — non OCRisée)"
        else:
            b["display"] = b.get("text") or ""; b["ocr"] = b.get("text") or "(vide)"
        b["crop"] = crop_b64(im, b["box"])          # image lisible du bloc
    blocks_js = json.dumps(data["blocks"], ensure_ascii=False)
    colors_js = json.dumps(COLORS, ensure_ascii=False)

    html = f"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8>
<title>Jumeau numérique — {esc(name)} ({esc(data['slug'])})</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;background:#15151c;color:#eee;font-family:Georgia,serif;overflow:hidden}}
body{{display:flex;flex-direction:column;height:100vh}}
#top{{height:32px;flex:0 0 32px;display:flex;align-items:center;gap:16px;padding:0 14px;background:#22222e;
  border-bottom:1px solid #333;font-family:sans-serif;font-size:12px}}
#top b{{font-size:13px}} #top span{{color:#9a9}}
#wrap{{flex:1;display:flex;min-height:0}}
#L,#R{{flex:1;overflow:auto;position:relative}}
#L{{background:#2a2a30;border-right:2px solid #444}}
#Limg{{position:relative;width:100%}} #Limg img{{width:100%;display:block}}
.box{{position:absolute;border:2px solid;opacity:.30;cursor:pointer;transition:opacity .1s}}
.box:hover,.box.hi{{opacity:.85;z-index:5}}
#R{{background:#e9e3d6}}
#page{{position:relative;width:100%;aspect-ratio:{W}/{H};background:#fbf7ee}}
.tb{{position:absolute;overflow:hidden;cursor:pointer;padding:1px 3px;border:1px solid transparent;line-height:1.12}}
.tb:hover,.tb.hi{{overflow:visible;background:rgba(255,247,200,.9);border-color:currentColor;z-index:6}}
.tb.header,.tb.titre{{font-weight:bold;display:flex;align-items:center}}          /* titres centrés (V) */
.tb.header{{font-family:'Times New Roman',serif}}
.tb.header p,.tb.titre p{{text-align:center;white-space:pre;word-break:normal}}   /* titres centrés, sans retour ligne auto (fit réduit la police) */
.tb.bloc_de_texte p,.tb.texte_isolé p{{text-align:justify;text-align-last:left}} /* corps justifié */
.tb.illustration{{background:repeating-linear-gradient(45deg,#ddd,#ddd 6px,#ccc 6px,#ccc 12px);
  color:#666;display:flex;align-items:center;justify-content:center;font-family:sans-serif;font-size:10px;border-color:#999}}
.tb p{{margin:0;white-space:normal;word-break:break-word;overflow:hidden;width:100%}}
/* panneau de contrôle bas */
#bottom{{flex:0 0 36vh;display:flex;border-top:2px solid #555;background:#1b1b22;min-height:0}}
.bside{{display:flex;flex-direction:column;min-height:0}}
#bcrop{{flex:0 0 46%;border-right:1px solid #333}}
#btext{{flex:1}}
.bhdr{{flex:0 0 24px;font-family:sans-serif;font-size:11px;color:#9ab;padding:0 10px;background:#232330;
  border-bottom:1px solid #333;display:flex;align-items:center;gap:8px}}
.bscroll{{flex:1;overflow:auto;min-height:0}}
#bcropscroll{{background:#3a3a3a;padding:8px;display:flex;justify-content:center;align-items:flex-start}}
#bimg{{max-width:100%;display:block;background:#fff;border:1px solid #000}}
#btxtscroll{{padding:9px 14px;white-space:pre-wrap;font-size:15px;line-height:1.5}}
.badge{{font-family:sans-serif;font-weight:700;font-size:11px;padding:1px 7px;border-radius:9px;color:#fff}}
</style></head><body>
<div id=top><b>{esc(name)}</b><span>{esc(data['slug'])} · {len(data['blocks'])} blocs</span>
  <span>← scan · jumeau OCR → · cliquez un bloc pour le contrôler en bas</span></div>
<div id=wrap>
  <div id=L><div id=Limg><img id=scan src="data:image/jpeg;base64,{b64}"></div></div>
  <div id=R><div id=page></div></div>
</div>
<div id=bottom>
  <div id=bcrop class=bside>
    <div class=bhdr id=bhdr>image du bloc — cliquez un bloc</div>
    <div class=bscroll id=bcropscroll><img id=bimg></div>
  </div>
  <div id=btext class=bside>
    <div class=bhdr>transcription OCR</div>
    <div class=bscroll id=btxtscroll><span id=btxt style="color:#888">— sélectionnez un bloc —</span></div>
  </div>
</div>
<script>
const B={blocks_js}, C={colors_js};
function build(){{
  const limg=document.getElementById('Limg'), page=document.getElementById('page');
  B.forEach((b,i)=>{{
    const [x0,y0,x1,y1]=b.nbox, col=C[b.class]||'#888';
    const box=document.createElement('div'); box.className='box'; box.dataset.i=i;
    box.style.cssText=`left:${{x0*100}}%;top:${{y0*100}}%;width:${{(x1-x0)*100}}%;height:${{(y1-y0)*100}}%;border-color:${{col}};background:${{col}}`;
    box.onclick=()=>sel(i); limg.appendChild(box);
    const tb=document.createElement('div'); tb.className='tb '+b.class.replace(/ /g,'_'); tb.dataset.i=i;
    tb.style.cssText=`left:${{x0*100}}%;top:${{y0*100}}%;width:${{(x1-x0)*100}}%;height:${{(y1-y0)*100}}%;color:${{col}}`;
    if(b.class==='illustration'){{ tb.textContent='🖼 illustration'; }}
    else {{ const p=document.createElement('p'); p.textContent=b.display||''; tb.appendChild(p); }}
    tb.onclick=()=>sel(i); page.appendChild(tb);
  }});
  fit();
}}
// cherche la plus grande police telle que le texte remplisse le bloc sans déborder
function fitOne(tb){{
  const p=tb.querySelector('p'); if(!p) return;
  const H=tb.clientHeight, W=tb.clientWidth;
  if(H<4||W<4) return;
  let lo=3, hi=Math.max(6,H);
  for(let k=0;k<12;k++){{
    const mid=(lo+hi)/2; p.style.fontSize=mid+'px';
    if(p.scrollHeight<=H+0.5 && p.scrollWidth<=W+0.5) lo=mid; else hi=mid;
  }}
  p.style.fontSize=lo.toFixed(2)+'px';
  // micro-ajustement : si la dernière lettre dépasse encore, réduire (max ~2px)
  let g=0;
  while(g<4 && p.scrollWidth>W-3){{ lo-=0.5; p.style.fontSize=lo.toFixed(2)+'px'; g++; }}
}}
function fit(){{ document.querySelectorAll('.tb').forEach(fitOne); }}
let cur=null;
function sel(i){{
  if(cur!=null) document.querySelectorAll(`[data-i="${{cur}}"]`).forEach(e=>e.classList.remove('hi'));
  cur=i; document.querySelectorAll(`[data-i="${{i}}"]`).forEach(e=>e.classList.add('hi'));
  const b=B[i], L=document.getElementById('L'), R=document.getElementById('R');
  L.scrollTo({{top:b.nbox[1]*document.getElementById('Limg').offsetHeight-L.clientHeight*.35,behavior:'smooth'}});
  R.scrollTo({{top:b.nbox[1]*document.getElementById('page').offsetHeight-R.clientHeight*.35,behavior:'smooth'}});
  // panneau bas : image lisible du bloc + transcription
  document.getElementById('bimg').src='data:image/jpeg;base64,'+b.crop;
  document.getElementById('bcropscroll').scrollTop=0;
  const col=C[b.class]||'#888';
  document.getElementById('bhdr').innerHTML=`<span class=badge style="background:${{col}}">${{b.class}}</span> image du bloc #${{i}}`;
  const t=document.getElementById('btxt'); t.textContent=b.ocr||''; t.style.color='#eee';
  document.getElementById('btxtscroll').scrollTop=0;
}}
new ResizeObserver(fit).observe(document.getElementById('page'));
const s=document.getElementById('scan'); s.complete?build():(s.onload=build);
</script></body></html>"""
    out.write_text(html, encoding="utf-8")
    print(f"jumeau -> {out}  ({out.stat().st_size//1024} Ko)")


if __name__ == "__main__":
    main()
