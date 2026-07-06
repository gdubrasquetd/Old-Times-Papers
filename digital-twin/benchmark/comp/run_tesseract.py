"""Tesseract multi-config sur le jeu de blocs -> comp/res_tesseract_<psm>_<prep>.json.
Env : bloc_detection (cv2). Tesseract via le binaire.
"""
import json, shutil, subprocess, sys
from pathlib import Path
import cv2, numpy as np
from PIL import Image   # lecture/écriture unicode-safe (noms accentués)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_lib import blocklist

HERE = Path(__file__).resolve().parent
TESS = shutil.which("tesseract") or r"C:/Program Files/Tesseract-OCR/tesseract.exe"
TMP = HERE / "_tess"; TMP.mkdir(exist_ok=True)


def binarize(path, dst):
    g = np.array(Image.open(path).convert("L"))
    g = cv2.medianBlur(g, 3)
    bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)
    Image.fromarray(bw).save(dst)
    return dst


def run(path, psm):
    r = subprocess.run([TESS, str(path), "stdout", "-l", "fra", "--psm", str(psm)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout


items = blocklist()
print(f"{len(items)} blocs", flush=True)
for psm in [3, 4, 6]:
    for prep in ["raw", "bin"]:
        out = {}
        for f, path, cls, gt in items:
            src = path if prep == "raw" else binarize(path, TMP / (Path(f).stem + "_bin.png"))
            out[f] = run(src, psm)
        label = f"tesseract_psm{psm}_{prep}"
        (HERE / f"res_{label}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {label} ok", flush=True)
