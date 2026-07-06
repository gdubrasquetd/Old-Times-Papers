"""Teste Kraken sur les TITRES en court-circuitant le segmenteur blla (qui plante
sur les gros glyphes). On découpe le titre en lignes par projection horizontale,
puis on passe chaque ligne à la reco Kraken via une Segmentation bbox directe.

Compare 3 variantes au Kraken-blla actuel :
  - lines-raw   : lignes découpées, image brute
  - lines-up2   : lignes découpées, upscale x2
  - whole       : tout le crop comme UNE ligne (ok pour titres mono-ligne)

Env : oldspapers (kraken). python kraken_titles.py
"""
from __future__ import annotations
import json, re, unicodedata
from pathlib import Path
import cv2, numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
TITLES = [m["file"] for m in manifest if m["class"] in ("titre", "header")]

from rapidfuzz.distance import Levenshtein

def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def cer(ref, hyp):
    r = norm(ref)
    return Levenshtein.distance(r, norm(hyp)) / len(r) if r else None


def split_lines(gray):
    """Bandes de lignes via projection horizontale de l'encre (texte sombre).
    On coupe sur les *creux* d'encre (interlignes), pas sur toute absence d'encre,
    pour ne pas fragmenter une ligne au niveau des jambages."""
    g = cv2.medianBlur(gray, 3)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)  # texte=blanc
    rows = (bw > 0).sum(axis=1).astype(float)
    H = len(rows)
    if rows.max() == 0:
        return [(0, H)]
    # lissage vertical pour stabiliser la projection
    k = max(3, H // 200) | 1
    rows = np.convolve(rows, np.ones(k) / k, mode="same")
    on = rows > rows.max() * 0.18          # "il y a du texte sur cette rangée"
    bands, i = [], 0
    while i < H:
        if on[i]:
            j = i
            while j < H and on[j]:
                j += 1
            if j - i >= max(6, H // 60):    # ignore filets/points parasites
                bands.append((i, j))
            i = j
        else:
            i += 1
    return bands or [(0, H)]


# ── Kraken ──
from kraken.lib import models as kmodels
from kraken import rpred
from kraken.containers import Segmentation, BBoxLine
REC = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
       r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
net = kmodels.load_any(REC)


def reco_line(pil_img):
    W, Hh = pil_img.size
    seg = Segmentation(type="bbox", imagename="", text_direction="horizontal-lr",
                       script_detection=False, regions={}, line_orders=[],
                       lines=[BBoxLine(id="l0", bbox=(0, 0, W, Hh), text=None)])
    try:
        for rec in rpred.rpred(net, pil_img, seg):
            return rec.prediction or ""
    except Exception:
        return ""
    return ""


def run(gray, mode):
    if mode == "whole":
        img = Image.fromarray(gray).convert("RGB")
        return reco_line(img)
    lines = split_lines(gray)
    out = []
    for (y0, y1) in lines:
        pad = int((y1 - y0) * 0.15)
        crop = gray[max(0, y0 - pad):min(gray.shape[0], y1 + pad), :]
        if mode == "lines-up2":
            crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        out.append(reco_line(Image.fromarray(crop).convert("RGB")))
    return "\n".join(out)


MODES = ["whole", "lines-raw", "lines-up2"]
rows_out = {}
for f in TITLES:
    gray = cv2.imread(str(HERE / "crops" / f), cv2.IMREAD_GRAYSCALE)
    rows_out[f] = {m: cer(gt[f], run(gray, "lines-raw" if m == "lines-raw" else m)) for m in MODES}

print("Kraken sur les titres — CER (plus bas = mieux)\n")
print(f"{'crop':<46}{'blla(actuel)':>13}{'whole':>9}{'lines-raw':>11}{'lines-up2':>11}{'#lignes':>8}")
for f in TITLES:
    blla = results.get(f, {}).get("kraken", {}).get("text")
    cb = cer(gt[f], blla) if blla is not None else None
    r = rows_out[f]
    nlines = len(split_lines(cv2.imread(str(HERE / "crops" / f), cv2.IMREAD_GRAYSCALE)))
    def fmt(x): return f"{x:>10.1%}" if x is not None else f"{'—':>10}"
    print(f"{f[:44]:<46}{fmt(cb):>13}{fmt(r['whole']):>9}{fmt(r['lines-raw']):>11}{fmt(r['lines-up2']):>11}{nlines:>8}")

# transcription détaillée (mode lines-up2) pour juger à l'oeil
print("\n--- sortie lines-up2 vs GT ---")
for f in TITLES:
    gray = cv2.imread(str(HERE / "crops" / f), cv2.IMREAD_GRAYSCALE)
    hyp = run(gray, "lines-up2").replace("\n", " / ")
    print(f"\n[{f}]")
    print(f"  GT : {gt[f][:120]}")
    print(f"  OCR: {hyp[:120]}")
