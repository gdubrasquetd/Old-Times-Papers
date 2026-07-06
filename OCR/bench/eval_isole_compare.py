"""Compare kraken (route actuelle) vs doctr sur les 'texte isolé' (dev + test).
Env : ocr_torch (doctr). python eval_isole_compare.py
"""
import json, re, unicodedata
from pathlib import Path
import numpy as np
from PIL import Image
from rapidfuzz.distance import Levenshtein
from doctr.models import ocr_predictor

HERE = Path(__file__).resolve().parent
T = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")

gt_dev = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
res_dev = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
res_test = json.loads((HERE / "results_test.json").read_text(encoding="utf-8"))
gt_test = {f.stem + ".png": f.read_text(encoding="utf-8").strip() for f in T.glob("*.txt")}


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def cer(ref, hyp):
    r = norm(ref); return Levenshtein.distance(r, norm(hyp)) / len(r) if r else None

model = ocr_predictor(pretrained=True)
def doctr_text(path):
    res = model([np.array(Image.open(path).convert("RGB"))])
    return res.render()

# (crop_path, gt, kraken_text)
items = []
for f, g in gt_dev.items():
    if "texte_isol" in f:
        items.append((HERE / "crops" / f, g, res_dev.get(f, {}).get("kraken", {}).get("text"), "dev", f))
for f, g in gt_test.items():
    if "texte_isol" in f:
        items.append((HERE / "crops_test" / f, g, res_test.get(f, {}).get("text"), "test", f))

print(f"{'set':5} {'kraken':>8} {'doctr':>8}   bloc")
kr, dc = [], []
for path, g, ktxt, s, f in items:
    ck = cer(g, ktxt) if ktxt is not None else None
    cd = cer(g, doctr_text(path))
    kr.append(ck); dc.append(cd)
    fk = f"{ck:.1%}" if ck is not None else "-"
    print(f"{s:5} {fk:>8} {cd:>7.1%}   {f}")

import statistics as st
print(f"\nmoyenne  kraken={st.mean([c for c in kr if c is not None]):.1%}   doctr={st.mean(dc):.1%}")
