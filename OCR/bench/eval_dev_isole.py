"""CER Kraken sur les 'texte isolé' du DEV (route de la pipeline pour cette classe)."""
import json, re, unicodedata
from pathlib import Path
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parent
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
manifest = {m["file"]: m for m in json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))}


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()


print("DEV - texte isolé (moteur = kraken) :")
for f, m in manifest.items():
    if m["class"] != "texte isolé":
        continue
    r = norm(gt.get(f, ""))
    hyp = results.get(f, {}).get("kraken", {}).get("text")
    if not r or hyp is None:
        print(f"  (pas de donnee) {f}"); continue
    cer = Levenshtein.distance(r, norm(hyp)) / len(r)
    print(f"  {cer:6.1%}  n={len(r):4}  {f}")
