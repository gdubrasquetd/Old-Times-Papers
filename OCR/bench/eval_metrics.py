"""Métriques multi-niveaux de la chaîne figée sur dev+test :
CER (caractère), WER (mot), précision mot, et % de phrases exactes.
Révèle les ratés de segmentation que le CER seul masque.
"""
import json, re, unicodedata
from pathlib import Path
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parent
T_TEST = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")
TEXT = {"bloc de texte", "titre", "texte isolé"}


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def sentences(s):
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", norm(s)) if x.strip()]

def metrics(gt, hyp):
    rg, rh = norm(gt), norm(hyp)
    if not rg:
        return None
    cer = Levenshtein.distance(rg, rh) / len(rg)
    wg, wh = rg.split(), rh.split()
    wer = Levenshtein.distance(wg, wh) / max(1, len(wg))
    # phrases exactes : combien de phrases GT retrouvées telles quelles
    sg, sh = sentences(gt), set(sentences(hyp))
    sent_ok = sum(1 for s in sg if s in sh) / len(sg) if sg else 0
    return {"cer": cer, "wer": wer, "ng": len(wg), "nc": len(rg),
            "sent_ok": sent_ok, "nsent": len(sg)}


# collecte dev + test (chaîne figée)
rows = []
# DEV
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
resd = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
mand = {m["file"]: m for m in json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))}
for f, m in mand.items():
    if m["class"] not in TEXT:
        continue
    eng = "kraken" if m["class"] == "bloc de texte" else "doctr"
    hyp = resd.get(f, {}).get(eng, {}).get("text")
    mt = metrics(gt.get(f, ""), hyp) if hyp is not None else None
    if mt:
        rows.append({**mt, "cls": m["class"], "eng": eng, "file": f})
# TEST
rest = json.loads((HERE / "results_test.json").read_text(encoding="utf-8"))
mant = {m["file"]: m for m in json.loads((HERE / "manifest_test40.json").read_text(encoding="utf-8"))}
for p in T_TEST.glob("*.txt"):
    f = p.stem + ".png"
    if f not in mant or mant[f]["class"] not in TEXT or f not in rest:
        continue
    mt = metrics(p.read_text(encoding="utf-8"), rest[f]["text"])
    if mt:
        rows.append({**mt, "cls": mant[f]["class"], "eng": rest[f]["engine"], "file": f})


def agg(rs, key, wkey):
    num = sum(r[key] * r[wkey] for r in rs); den = sum(r[wkey] for r in rs)
    return num / den if den else 0

print(f"=== {len(rows)} blocs de texte (dev+test), chaîne figée ===\n")
print(f"{'niveau':<22}{'micro':>9}{'macro':>9}")
print(f"{'CER (caractère)':<22}{agg(rows,'cer','nc'):>8.1%}{sum(r['cer'] for r in rows)/len(rows):>9.1%}")
print(f"{'WER (mot)':<22}{agg(rows,'wer','ng'):>8.1%}{sum(r['wer'] for r in rows)/len(rows):>9.1%}")
print(f"{'précision mot':<22}{1-agg(rows,'wer','ng'):>8.1%}")
print(f"{'phrases exactes':<22}{agg(rows,'sent_ok','nsent'):>8.1%}")

clean = sum(1 for r in rows if r["wer"] < 0.10)
blown = [r for r in rows if r["wer"] > 0.30]
print(f"\nblocs 'propres' (WER<10%) : {clean}/{len(rows)} = {clean/len(rows):.0%}")
print(f"blocs cassés (WER>30%)    : {len(blown)}")
print("\nPires blocs (WER) — souvent des ratés de segmentation :")
for r in sorted(rows, key=lambda x: -x["wer"])[:8]:
    print(f"  CER {r['cer']:5.1%}  WER {r['wer']:5.1%}  [{r['eng']:6}] {r['cls']:14} {r['file'][:42]}")
