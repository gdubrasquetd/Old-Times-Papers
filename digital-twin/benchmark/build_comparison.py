"""Galerie comparative : crop original <-> texte OCR (+ GT & CER si dispo), sur ~100+
blocs de texte issus du dev, du test et d'Excelsior. -> comparison_100.html
Env : oldspapers (PIL + rapidfuzz). python build_comparison.py
"""
import base64, io, json, re, unicodedata
from pathlib import Path
from PIL import Image
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parent
TWIN = HERE.parent / "twin" / "out" / "excelsior_1924-05-28" / "blocks.json"
T_TEST = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")
TEXT = {"bloc de texte", "titre", "texte isolé"}


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def cer(g, h):
    r = norm(g)
    return Levenshtein.distance(r, norm(h)) / len(r) if r else None

def thumb(im, maxh=240):
    if im.height > maxh:
        im = im.resize((max(1, im.width * maxh // im.height), maxh), Image.LANCZOS)
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=78)
    return base64.b64encode(b.getvalue()).decode()

entries = []   # {b64, cls, engine, ocr, gt, cer, src}

# --- DEV (crops/, gt.json, results.json : moteur = kraken pour corps, doctr sinon) ---
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
resd = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
mand = {m["file"]: m for m in json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))}
for f, m in mand.items():
    if m["class"] not in TEXT or not (HERE / "crops" / f).exists():
        continue
    eng = "kraken" if m["class"] == "bloc de texte" else "doctr"
    ocr = resd.get(f, {}).get(eng, {}).get("text")
    if ocr is None:
        continue
    entries.append({"b64": thumb(Image.open(HERE / "crops" / f)), "cls": m["class"], "engine": eng,
                    "ocr": ocr, "gt": gt.get(f), "cer": cer(gt.get(f), ocr), "src": "dev"})

# --- TEST (crops_test/, GT dans t_test, results_test.json) ---
rest = json.loads((HERE / "results_test.json").read_text(encoding="utf-8"))
mant = {m["file"]: m for m in json.loads((HERE / "manifest_test40.json").read_text(encoding="utf-8"))}
gtt = {p.stem + ".png": p.read_text(encoding="utf-8").strip() for p in T_TEST.glob("*.txt")}
for f, m in mant.items():
    if m["class"] not in TEXT or f not in rest or not (HERE / "crops_test" / f).exists():
        continue
    ocr = rest[f]["text"]
    entries.append({"b64": thumb(Image.open(HERE / "crops_test" / f)), "cls": m["class"],
                    "engine": rest[f]["engine"], "ocr": ocr, "gt": gtt.get(f),
                    "cer": cer(gtt.get(f), ocr), "src": "test"})

# --- EXCELSIOR (jumeau, pas de GT) ---
if TWIN.exists():
    tw = json.loads(TWIN.read_text(encoding="utf-8"))
    full = Image.open(tw["image"]).convert("RGB")
    for b in tw["blocks"]:
        if b["class"] not in TEXT or not (b.get("text") or "").strip():
            continue
        x0, y0, x1, y1 = b["box"]
        entries.append({"b64": thumb(full.crop((x0, y0, x1, y1))), "cls": b["class"],
                        "engine": b.get("engine", "?"), "ocr": b["text"], "gt": None,
                        "cer": None, "src": "excelsior"})

# ordre : dev+test (avec GT) d'abord, puis excelsior
entries.sort(key=lambda e: (e["gt"] is None, e["cls"]))

def esc(s): return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def cerbadge(c):
    if c is None: return ""
    col = "#2a9d8f" if c < .05 else "#e9c46a" if c < .12 else "#e76f51" if c < .5 else "#c0392b"
    return f'<span class=cer style="background:{col}">CER {c:.1%}</span>'

cards = ""
for e in entries:
    gt_html = f'<div class=gt><b>vérité terrain</b><br>{esc(e["gt"])}</div>' if e["gt"] else ""
    cards += f"""<div class=card>
      <div class=meta><span class=cls>{esc(e['cls'])}</span>
        <span class=eng>{esc(e['engine'])}</span><span class=src>{esc(e['src'])}</span>{cerbadge(e['cer'])}</div>
      <div class=row><div class=img><img src="data:image/jpeg;base64,{e['b64']}"></div>
      <div class=txts><div class=ocr><b>OCR</b><br>{esc(e['ocr'])}</div>{gt_html}</div></div></div>"""

ncer = [e["cer"] for e in entries if e["cer"] is not None]
avg = f"CER moyen (blocs avec GT) : {sum(ncer)/len(ncer):.1%} sur {len(ncer)} blocs" if ncer else ""
html = f"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8><title>Comparatif OCR — {len(entries)} blocs</title><style>
body{{font-family:sans-serif;background:#16161c;color:#e6e6e6;margin:0;padding:16px}}
h1{{font-size:18px}} .sub{{color:#9a9;font-size:13px;margin-bottom:14px}}
.card{{background:#22222b;border:1px solid #383844;border-radius:7px;margin-bottom:12px;overflow:hidden}}
.meta{{display:flex;gap:8px;align-items:center;padding:6px 10px;background:#2a2a35;font-size:12px}}
.cls{{color:#8bd;font-weight:600}} .eng{{color:#b9a}} .src{{color:#788;margin-right:auto}}
.cer{{color:#111;font-weight:700;padding:1px 7px;border-radius:9px;font-size:11px}}
.row{{display:flex;gap:12px;padding:10px}}
.img{{flex:0 0 300px;background:#333;border-radius:4px;overflow:auto;max-height:250px}}
.img img{{width:100%;display:block;background:#fff}}
.txts{{flex:1;display:flex;flex-direction:column;gap:8px;min-width:0}}
.ocr,.gt{{font-family:Georgia,serif;font-size:13.5px;line-height:1.4;white-space:pre-wrap;
  border-radius:4px;padding:7px 9px;border:1px solid #444}}
.ocr{{background:#1c1c24}} .gt{{background:#14201c;border-color:#2a9d8f}}
.ocr b,.gt b{{font-family:sans-serif;font-size:10px;color:#9ab;text-transform:uppercase}}
</style></head><body>
<h1>Comparatif OCR — {len(entries)} blocs de texte</h1>
<div class=sub>Chaîne figée : corps→Kraken, titre/texte isolé→doctr. {avg}. Dev+test ont la vérité terrain ; Excelsior = inédit sans GT.</div>
{cards}</body></html>"""
out = HERE / "comparison_100.html"
out.write_text(html, encoding="utf-8")
print("ok", out, len(entries), "blocs")
