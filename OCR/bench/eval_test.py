"""Évalue la CHAÎNE FIGÉE sur le jeu de test (généralisation).
GT = mes transcriptions (t_test/), sorties = results_test.json (routage figé).
Compare micro/macro au dev (2.8% / 3.5%).
"""
import json, re, unicodedata
from pathlib import Path
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parent
T = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")
results = json.loads((HERE / "results_test.json").read_text(encoding="utf-8"))
manifest = {m["file"]: m for m in json.loads((HERE / "manifest_test40.json").read_text(encoding="utf-8"))}

# GT depuis les fichiers
gt = {}
for f in T.glob("*.txt"):
    gt[f.stem + ".png"] = f.read_text(encoding="utf-8").strip()

# assemble un gt_test.json pour référence
(HERE / "gt_test.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()


rows = []
for f, g in gt.items():
    r = norm(g)
    if not r:
        continue
    hyp = results.get(f, {}).get("text")
    if hyp is None:
        print("PAS DE SORTIE:", f); continue
    ed = Levenshtein.distance(r, norm(hyp))
    rows.append({"file": f, "class": manifest[f]["class"], "engine": results[f]["engine"],
                 "paper": f.rsplit("__", 2)[0], "n": len(r), "ed": ed, "cer": ed / len(r)})


def micro(rs): return sum(x["ed"] for x in rs) / sum(x["n"] for x in rs) if rs else 0
def macro(rs): return sum(x["cer"] for x in rs) / len(rs) if rs else 0

print(f"=== JEU DE TEST : {len(rows)} blocs, {sum(r['n'] for r in rows)} caractères ===\n")
print(f"GLOBAL (tout)          micro={micro(rows):.2%}   macro={macro(rows):.1%}")
# ce qui compte vraiment : hors header (nom du journal, connu) et hors illustration
content = [r for r in rows if r["class"] not in ("header", "illustration")]
print(f"CONTENU (corps+titre)  micro={micro(content):.2%}   macro={macro(content):.1%}   ({len(content)} blocs)")
print(f"(rappel dev, contenu:  micro~2.8%   macro~3.5%)\n")

print("Par classe (moteur figé) :")
for cls in ["bloc de texte", "titre", "header"]:
    rs = [r for r in rows if r["class"] == cls]
    if rs:
        eng = rs[0]["engine"]
        print(f"  {cls:14} [{eng:12}] n={len(rs):2}  micro={micro(rs):.2%}  macro={macro(rs):.1%}")

print("\nPar journal :")
for p in sorted({r["paper"] for r in rows}):
    rs = [r for r in rows if r["paper"] == p]
    print(f"  {p:32} n={len(rs)}  micro={micro(rs):.2%}")

print("\nPires blocs (CER décroissant) :")
for r in sorted(rows, key=lambda x: -x["cer"])[:8]:
    print(f"  {r['cer']:6.1%}  {r['class']:14} {r['file']}")
