"""Rend results.json en une page HTML de comparaison : pour chaque crop de bloc,
l'image + la transcription de chaque moteur côte à côte.

    python render_results.py   ->   OCR/bench/comparison.html
"""
import json, html
from pathlib import Path

HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
by_file = {m["file"]: m for m in manifest}

engines = sorted({e for r in results.values() for e in r})

rows = []
for f in [m["file"] for m in manifest]:
    meta = by_file[f]
    cells = []
    for eng in engines:
        d = results.get(f, {}).get(eng)
        txt = html.escape(d["text"]).strip() if d else "<i>(non traité)</i>"
        secs = f"{d['secs']}s" if d else ""
        cells.append(f"<td><div class='eng'>{eng} <span class='t'>{secs}</span></div>"
                     f"<pre>{txt or '<i>(vide)</i>'}</pre></td>")
    rows.append(
        f"<tr><td class='imgcell'><div class='cls'>{html.escape(meta['class'])} "
        f"<span class='t'>conf {meta['conf']} · {meta['w']}×{meta['h']}px</span></div>"
        f"<img src='crops/{f}'></td>{''.join(cells)}</tr>")

head = "<th>bloc détecté</th>" + "".join(f"<th>{e}</th>" for e in engines)
groups = sorted({m['image'] for m in manifest})
html_doc = f"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8>
<title>Comparaison OCR par bloc</title><style>
body{{font-family:sans-serif;background:#1e1e1e;color:#ddd;margin:0;padding:16px}}
h1{{font-size:18px}} .sub{{color:#999;font-size:13px;margin-bottom:12px}}
table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #444;vertical-align:top;padding:8px;text-align:left}}
th{{background:#2a2a2a;position:sticky;top:0}}
.imgcell{{width:340px}} .imgcell img{{max-width:330px;background:#fff;border:1px solid #555}}
.cls,.eng{{font-weight:bold;margin-bottom:4px}} .t{{color:#888;font-weight:normal;font-size:11px}}
pre{{white-space:pre-wrap;font-family:Consolas,monospace;font-size:12px;margin:0;max-height:340px;overflow:auto}}
</style></head><body>
<h1>Comparaison OCR sur les blocs détectés (multiclass_yolo11s_v3)</h1>
<div class="sub">Unes : {', '.join(groups)} — moteurs : {', '.join(engines) or '(aucun)'}</div>
<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
(HERE / "comparison.html").write_text(html_doc, encoding="utf-8")
print("->", HERE / "comparison.html")
