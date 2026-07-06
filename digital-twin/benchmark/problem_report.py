"""Rapport visuel des cas OCR problématiques : pour chaque bloc, l'image du crop,
la vérité terrain et la sortie de chaque moteur avec son CER. Trie du pire au
meilleur (selon le meilleur moteur du bloc) et génère problems.html.

    python problem_report.py   ->   ouvre OCR/bench/problems.html
"""
import base64, io, json, re, unicodedata
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
CLASS = {m["file"]: m["class"] for m in manifest}


def norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("«", '"').replace("»", '"')
    s = unicodedata.normalize("NFC", s)
    return re.sub(r"\s+", " ", s).strip().lower()


try:
    from rapidfuzz.distance import Levenshtein
    def lev(a, b):
        return Levenshtein.distance(a, b)
except Exception:                       # repli pur Python
    def lev(a, b):
        if a == b:
            return 0
        if not a or not b:
            return len(a) or len(b)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]


def cer(ref, hyp):
    r = norm(ref)
    return None if not r else lev(r, norm(hyp)) / len(r)


engines = sorted({e for f in gt for e in results.get(f, {})})
rows = []
for f in gt:
    if not norm(gt[f]):
        continue
    cers = {e: cer(gt[f], results.get(f, {}).get(e, {}).get("text")) for e in engines}
    valid = [c for c in cers.values() if c is not None]
    rows.append({"file": f, "class": CLASS.get(f, "?"), "gt": gt[f], "cers": cers,
                 "best": min(valid) if valid else 9})
rows.sort(key=lambda r: -r["best"])   # pire d'abord


def cell(c):
    if c is None:
        return "—"
    col = "#2a9d8f" if c < 0.05 else "#e9c46a" if c < 0.12 else "#e76f51" if c < 0.5 else "#c0392b"
    return f'<span style="color:{col};font-weight:600">{c:.1%}</span>'


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def thumb_b64(f, maxh=1100):
    im = Image.open(HERE / "crops" / f).convert("RGB")
    if im.height > maxh:
        im = im.resize((max(1, im.width * maxh // im.height), maxh), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


cards = []
for r in rows:
    f = r["file"]
    b64 = thumb_b64(f)
    outs = ""
    for e in sorted(engines, key=lambda e: (r["cers"][e] is None, r["cers"][e] if r["cers"][e] is not None else 9)):
        txt = results.get(f, {}).get(e, {}).get("text", "")
        outs += (f'<div class=eng><div class=eh>{e} — CER {cell(r["cers"][e])}</div>'
                 f'<div class=et>{esc(txt)}</div></div>')
    cards.append(f"""<div class=card>
      <div class=head><b>{esc(f)}</b> <span class=cls>{esc(r['class'])}</span>
        <span class=best>meilleur CER : {cell(r['best'])}</span></div>
      <div class=body>
        <div class=imgcol><img src="data:image/jpeg;base64,{b64}"></div>
        <div class=txtcol>
          <div class=gt><div class=eh>VÉRITÉ TERRAIN</div><div class=et>{esc(r['gt'])}</div></div>
          {outs}
        </div>
      </div></div>""")

summary = "".join(
    f"<tr><td class=l>{esc(r['file'])}</td><td>{esc(r['class'])}</td>"
    + "".join(f"<td>{cell(r['cers'][e])}</td>" for e in engines) + "</tr>"
    for r in rows)

html = f"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8><title>Cas OCR problématiques</title>
<style>
body{{font-family:sans-serif;background:#1e1e1e;color:#ddd;margin:0;padding:18px}}
h1{{font-size:19px}} .sub{{color:#999;font-size:13px;margin-bottom:14px}}
table{{border-collapse:collapse;font-size:12px;margin-bottom:26px}}
th,td{{border:1px solid #444;padding:3px 7px;text-align:center}} td.l{{text-align:left;color:#bbb}}
th{{background:#2a2a3a}}
.card{{border:1px solid #444;border-radius:7px;margin-bottom:16px;overflow:hidden;background:#252530}}
.head{{background:#2a2a3a;padding:7px 11px;font-size:13px;display:flex;gap:14px;align-items:center}}
.cls{{color:#8bd}} .best{{margin-left:auto;color:#aaa}}
.body{{display:flex;gap:12px;padding:11px}}
.imgcol{{flex:0 0 300px;max-height:460px;overflow:auto;background:#333;border-radius:4px}}
.imgcol img{{width:100%;display:block;background:#fff}}
.txtcol{{flex:1;min-width:0;display:flex;flex-direction:column;gap:8px}}
.eh{{font-size:11px;color:#9ab;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px}}
.et{{font-family:Georgia,serif;font-size:13px;line-height:1.4;white-space:pre-wrap;
  background:#1b1b22;border:1px solid #383844;border-radius:4px;padding:6px 8px;max-height:150px;overflow:auto}}
.gt .et{{border-color:#2a9d8f}} .gt .eh{{color:#2a9d8f}}
</style></head><body>
<h1>Cas OCR problématiques — triés du pire au meilleur</h1>
<div class=sub>Couleur du CER : <span style="color:#2a9d8f">&lt;5%</span> ·
  <span style="color:#e9c46a">&lt;12%</span> · <span style="color:#e76f51">&lt;50%</span> ·
  <span style="color:#c0392b">échec</span>. Le « meilleur CER » = le moteur le plus juste sur ce bloc.</div>
<table><tr><th class=l>bloc</th><th>classe</th>{"".join(f"<th>{e}</th>" for e in engines)}</tr>{summary}</table>
{"".join(cards)}
</body></html>"""

out = HERE / "problems.html"
out.write_text(html, encoding="utf-8")
print("->", out)
