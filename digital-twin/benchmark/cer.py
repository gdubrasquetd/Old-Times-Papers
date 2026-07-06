"""Calcule le CER (Character Error Rate) de chaque moteur OCR contre la vérité
terrain (gt.json), sur les blocs qui ont une GT.

CER = distance de Levenshtein(GT, OCR) / longueur(GT), sur texte normalisé
(minuscules, espaces/retours-ligne compactés). 0 = parfait, plus c'est bas mieux.

    python cer.py
"""
import json, re, unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("«", '"').replace("»", '"')
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


MIN_CHARS = 200   # seuil : la moyenne ne compte que les blocs substantiels

engines = sorted({e for f in gt for e in results.get(f, {})})
rows = {e: [] for e in engines}
per_block = []

for f in gt:
    ref = norm(gt[f])
    if not ref:
        continue
    line = {"file": f, "n": len(ref)}
    for e in engines:
        hyp = results.get(f, {}).get(e, {}).get("text")
        if hyp is None:
            line[e] = None
            continue
        c = lev(ref, norm(hyp)) / len(ref)
        line[e] = c
        if len(ref) >= MIN_CHARS:      # bloc substantiel -> compte dans la moyenne
            rows[e].append(c)
    per_block.append(line)

nsub = sum(1 for l in per_block if l["n"] >= MIN_CHARS)
print(f"CER par moteur (moyenne sur {nsub} blocs substantiels >={MIN_CHARS} car.) - plus bas = meilleur\n")
print(f"{'moteur':<12} {'CER moyen':>10}   {'blocs':>5}")
for e in sorted(engines, key=lambda e: sum(rows[e]) / len(rows[e]) if rows[e] else 9):
    if rows[e]:
        print(f"{e:<12} {sum(rows[e])/len(rows[e]):>9.1%}   {len(rows[e]):>5}")

print("\nDétail par bloc :")
hdr = "bloc".ljust(46) + "".join(f"{e:>10}" for e in engines)
print(hdr)
for line in per_block:
    cells = "".join((f"{line[e]:>9.1%}" if line[e] is not None else f"{'—':>10}") for e in engines)
    print(f"{line['file'][:44]:<46}{cells}")
