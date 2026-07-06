"""Compare Kraken 'blla' (segmenteur pleine page, actuel) vs 'lignes par projection'
(le bloc étant déjà isolé par YOLO) sur les blocs de CORPS dev+test.
Mesure CER ET WER. Env : oldspapers. python eval_kraken_lines.py
"""
import json, re, unicodedata
from pathlib import Path
import cv2, numpy as np
from PIL import Image
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parent
T_TEST = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def cer(g, h):
    r = norm(g); return Levenshtein.distance(r, norm(h)) / len(r) if r else 0

def wer(g, h):
    wg = norm(g).split(); return Levenshtein.distance(wg, norm(h).split()) / max(1, len(wg))


def split_lines(gray):
    g = cv2.medianBlur(gray, 3)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rows = (bw > 0).sum(axis=1).astype(float); H = len(rows)
    if rows.max() == 0:
        return [(0, H)]
    k = max(3, H // 300) | 1
    rows = np.convolve(rows, np.ones(k) / k, mode="same")
    on = rows > rows.max() * 0.10
    bands, i = [], 0
    while i < H:
        if on[i]:
            j = i
            while j < H and on[j]:
                j += 1
            if j - i >= max(6, H // 120):
                bands.append((i, j))
            i = j
        else:
            i += 1
    return bands or [(0, H)]


from kraken.lib import models as kmodels
from kraken import rpred
from kraken.containers import Segmentation, BBoxLine
REC = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
       r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
net = kmodels.load_any(REC)


def reco_lines(gray):
    out = []
    for (y0, y1) in split_lines(gray):
        pad = int((y1 - y0) * 0.12)
        crop = gray[max(0, y0 - pad):min(gray.shape[0], y1 + pad), :]
        pil = Image.fromarray(crop).convert("RGB")
        seg = Segmentation(type="bbox", imagename="", text_direction="horizontal-lr",
                           script_detection=False, regions={}, line_orders=[],
                           lines=[BBoxLine(id="l0", bbox=(0, 0, pil.width, pil.height), text=None)])
        try:
            for rec in rpred.rpred(net, pil, seg):
                out.append(rec.prediction or ""); break
        except Exception:
            out.append("")
    return "\n".join(out)


# collecte des blocs de corps + GT + sortie blla (depuis results)
items = []   # (crop_path, gt, blla_text)
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
resd = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
mand = {m["file"]: m for m in json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))}
for f, m in mand.items():
    if m["class"] == "bloc de texte" and (HERE / "crops" / f).exists():
        items.append((HERE / "crops" / f, gt.get(f, ""), resd.get(f, {}).get("kraken", {}).get("text", "")))
rest = json.loads((HERE / "results_test.json").read_text(encoding="utf-8"))
mant = {m["file"]: m for m in json.loads((HERE / "manifest_test40.json").read_text(encoding="utf-8"))}
for p in T_TEST.glob("*bloc_de_texte.txt"):
    f = p.stem + ".png"
    if f in mant and (HERE / "crops_test" / f).exists() and f in rest:
        items.append((HERE / "crops_test" / f, p.read_text(encoding="utf-8"), rest[f]["text"]))

cb, wb, cl, wl = [], [], [], []
worst = []
for path, g, blla in items:
    if not norm(g):
        continue
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    lines = reco_lines(gray)
    cb.append(cer(g, blla)); wb.append(wer(g, blla))
    cl.append(cer(g, lines)); wl.append(wer(g, lines))
    worst.append((wer(g, blla), wer(g, lines), path.name))

n = len(cb)
print(f"=== {n} blocs de corps (dev+test) — blla vs lignes-projection ===\n")
print(f"{'métrique':<10}{'blla':>10}{'lignes':>10}")
print(f"{'CER moy':<10}{sum(cb)/n:>9.1%}{sum(cl)/n:>10.1%}")
print(f"{'WER moy':<10}{sum(wb)/n:>9.1%}{sum(wl)/n:>10.1%}")
print(f"\n{'blocs WER>30%':<14}{sum(1 for x in wb if x>.3):>6}{sum(1 for x in wl if x>.3):>10}")
print("\nblocs les plus améliorés (WER blla -> lignes) :")
for wbb, wll, name in sorted(worst, key=lambda x: x[1] - x[0])[:6]:
    print(f"  {wbb:5.1%} -> {wll:5.1%}   {name[:46]}")
