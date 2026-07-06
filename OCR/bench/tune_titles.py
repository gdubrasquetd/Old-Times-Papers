"""Sweep de configurations sur les crops TITRES/HEADERS (le point faible du banc).
Teste, pour Tesseract, une matrice prétraitement x --psm et mesure le CER vs gt.json.
But : vérifier si les mauvais scores sur les titres viennent d'une mauvaise config
(psm/prétraitement) plutôt que d'une incapacité du moteur.

Env : bloc_detection (cv2). Tesseract via le binaire.
    python tune_titles.py
"""
from __future__ import annotations
import json, re, shutil, subprocess, unicodedata
from pathlib import Path
import cv2, numpy as np

HERE = Path(__file__).resolve().parent
gt = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
TESS = shutil.which("tesseract") or r"C:/Program Files/Tesseract-OCR/tesseract.exe"

# crops titres / headers uniquement
TITLES = [m["file"] for m in manifest if m["class"] in ("titre", "header")]

try:
    from rapidfuzz.distance import Levenshtein
    lev = Levenshtein.distance
except Exception:
    def lev(a, b):
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            prev = cur
        return prev[-1]


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    s = unicodedata.normalize("NFC", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def cer(ref, hyp):
    r = norm(ref)
    return lev(r, norm(hyp)) / len(r) if r else None


# ── variantes de prétraitement (renvoient un chemin PNG temporaire) ──
PRE = HERE / "_tune"; PRE.mkdir(exist_ok=True)

def prep_raw(g):
    return g

def prep_bin(g):
    g = cv2.medianBlur(g, 3)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)

def prep_upscale(g):
    return cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

def prep_bin_upscale(g):
    return prep_upscale(prep_bin(g))

def prep_otsu(g):
    g = cv2.medianBlur(g, 3)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return b

PREPROCS = {"raw": prep_raw, "bin": prep_bin, "otsu": prep_otsu,
            "up2": prep_upscale, "bin+up2": prep_bin_upscale}
PSMS = [3, 6, 7, 11, 12]


def tess(img_path, psm):
    out = subprocess.run([TESS, str(img_path), "stdout", "-l", "fra", "--psm", str(psm)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout


results = {}   # (prep, psm) -> list de CER
for f in TITLES:
    g = cv2.imread(str(HERE / "crops" / f), cv2.IMREAD_GRAYSCALE)
    for pname, pfn in PREPROCS.items():
        img = pfn(g)
        p = PRE / f"{Path(f).stem}__{pname}.png"
        cv2.imwrite(str(p), img)
        for psm in PSMS:
            c = cer(gt[f], tess(p, psm))
            results.setdefault((pname, psm), {})[f] = c

# ── tableau récap : CER moyen par (prep, psm) ──
print(f"Sweep Tesseract sur {len(TITLES)} crops titres/headers — CER moyen (plus bas = mieux)\n")
print(f"{'prétraitement':<12} " + "".join(f"psm{psm:>3}   " for psm in PSMS))
best = None
for pname in PREPROCS:
    cells = ""
    for psm in PSMS:
        vals = [v for v in results[(pname, psm)].values() if v is not None]
        m = sum(vals) / len(vals)
        cells += f"{m:>6.1%}   "
        if best is None or m < best[0]:
            best = (m, pname, psm)
    print(f"{pname:<12} {cells}")
print(f"\nMeilleure config : {best[1]} + psm{best[2]}  ->  CER moyen {best[0]:.1%}")
print("(rappel : config actuelle du banc = raw + psm6)\n")

# détail par crop pour la meilleure config
bp, bpsm = best[1], best[2]
print(f"Détail (config {bp}+psm{bpsm}) vs actuel (raw+psm6) :")
print(f"{'crop':<48}{'actuel':>9}{'tuné':>9}")
for f in TITLES:
    a = results[("raw", 6)][f]; t = results[(bp, bpsm)][f]
    print(f"{f[:46]:<48}{a:>8.1%}{t:>9.1%}")
