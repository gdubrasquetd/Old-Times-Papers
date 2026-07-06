"""Grand comparatif : pour chaque res_*.json (moteur+config) et chaque post-traitement,
CER + WER par classe. Sort un tableau trié + comparison.html.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_lib import blocklist, POSTPROC, cer, wer

HERE = Path(__file__).resolve().parent
items = blocklist()
GT = {f: gt for f, _, _, gt in items}
CLS = {f: c for f, _, c, _ in items}
CLASSES = ["bloc de texte", "titre", "texte isolé"]

results = {}
for p in sorted(HERE.glob("res_*.json")):
    results[p.stem[4:]] = json.loads(p.read_text(encoding="utf-8"))


def micro(files, out, fn):
    num = den = 0
    for f in files:
        h = out.get(f)
        if h is None:
            continue
        from eval_lib import norm
        r = norm(GT[f])
        if not r:
            continue
        if fn is wer:
            g = r.split(); num += (wer(GT[f], h) or 0) * len(g); den += len(g)
        else:
            num += (cer(GT[f], h) or 0) * len(r); den += len(r)
    return num / den if den else None


rows = []
for label, out in results.items():
    for pp_name, pp in POSTPROC.items():
        o = {f: pp(t) for f, t in out.items()}
        row = {"engine": label, "pp": pp_name}
        for c in CLASSES:
            fs = [f for f in GT if CLS[f] == c]
            row[f"cer_{c}"] = micro(fs, o, cer)
            row[f"wer_{c}"] = micro(fs, o, wer)
        row["wer_all"] = micro(list(GT), o, wer)
        row["cer_all"] = micro(list(GT), o, cer)
        rows.append(row)

rows.sort(key=lambda r: r["wer_all"] if r["wer_all"] is not None else 9)

def fmt(x): return f"{x:.1%}" if x is not None else "—"
print(f"{'moteur+config':<26}{'post':<9}{'WER corps':>10}{'WER titre':>10}{'WER isolé':>10}{'WER tout':>10}{'CER tout':>10}")
for r in rows:
    print(f"{r['engine']:<26}{r['pp']:<9}"
          f"{fmt(r['wer_bloc de texte']):>10}{fmt(r['wer_titre']):>10}{fmt(r['wer_texte isolé']):>10}"
          f"{fmt(r['wer_all']):>10}{fmt(r['cer_all']):>10}")

# HTML
def cell(x, good=.05, ok=.12):
    if x is None: return '<td>—</td>'
    col = "#2a9d8f" if x < good else "#e9c46a" if x < ok else "#e76f51" if x < .5 else "#c0392b"
    return f'<td style="color:{col};font-weight:600">{x:.1%}</td>'
trs = ""
for r in rows:
    trs += ("<tr><td class=l>" + r["engine"] + "</td><td>" + r["pp"] + "</td>"
            + cell(r["wer_bloc de texte"]) + cell(r["wer_titre"]) + cell(r["wer_texte isolé"])
            + cell(r["wer_all"]) + cell(r["cer_all"], .03, .07) + "</tr>")
html = f"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8><title>Grand comparatif OCR</title><style>
body{{font-family:sans-serif;background:#16161c;color:#eee;padding:18px}}
table{{border-collapse:collapse;font-size:13px}} th,td{{border:1px solid #444;padding:4px 9px;text-align:right}}
td.l,th.l{{text-align:left}} th{{background:#2a2a3a;position:sticky;top:0}} tr:hover{{background:#22222c}}
h1{{font-size:19px}} .s{{color:#9a9;font-size:13px;margin-bottom:12px}}</style></head><body>
<h1>Grand comparatif OCR — {len(results)} configs × {len(POSTPROC)} post-traitements</h1>
<div class=s>Trié par WER global croissant. {len(items)} blocs (dev+test). Vert&lt;5% jaune&lt;12% orange&lt;50% rouge=échec.</div>
<table><tr><th class=l>moteur+config</th><th>post</th><th>WER corps</th><th>WER titre</th><th>WER isolé</th><th>WER tout</th><th>CER tout</th></tr>{trs}</table>
</body></html>"""
(HERE / "comparison.html").write_text(html, encoding="utf-8")
print(f"\n-> {HERE/'comparison.html'}")
