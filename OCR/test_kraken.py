"""
Test Kraken CATMuS-Print sur un crop de journal 1930.
Usage : conda run -n oldspapers python OCR/test_kraken.py
"""
import pathlib, sys, time
from PIL import Image

ROOT       = pathlib.Path(__file__).parent.parent
IMG_PATH   = ROOT / "cache" / "ocr_img" / "bpt6k412758h.jpg"
MODEL_PATH = pathlib.Path(
    r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
    r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel"
)
VERIFY_DIR = ROOT / "cache" / "verify"
VERIFY_DIR.mkdir(parents=True, exist_ok=True)

if not IMG_PATH.exists():
    print("Image manquante — lance verify_paddle.py d'abord"); sys.exit(1)
if not MODEL_PATH.exists():
    print(f"Modèle introuvable : {MODEL_PATH}"); sys.exit(1)

img = Image.open(IMG_PATH).convert("RGB")
W, H = img.size
print(f"Image : {W}×{H}", file=sys.stderr)

# ── On teste sur 3 crops différents ────────────────────────────────────────────
crops = {
    "col1_article":  (0,          int(.257*H), int(.15*W),  int(.47*H)),
    "titre_croix":   (int(.56*W), 0,           int(.70*W),  int(.09*H)),
    "col7_longtext": (int(.83*W), int(.12*H),  W,           int(.39*H)),
}

print("\nChargement Kraken CATMuS-Print...", file=sys.stderr)
t0 = time.time()
from kraken import blla, rpred
from kraken.lib import models
net = models.load_any(str(MODEL_PATH))
print(f"  modèle chargé en {time.time()-t0:.1f}s", file=sys.stderr)

for name, box in crops.items():
    crop = img.crop(box)
    print(f"\n{'═'*60}", file=sys.stderr)
    print(f"Crop : {name}  {crop.size}", file=sys.stderr)

    t1 = time.time()
    # blla = Baseline Layout Analysis (segmentation neurale)
    seg = blla.segment(crop, device="cpu")
    n_lines = len(seg.lines)
    print(f"  blla : {n_lines} lignes en {time.time()-t1:.1f}s", file=sys.stderr)

    if n_lines == 0:
        print("  (aucune ligne détectée)", file=sys.stderr)
        continue

    t2 = time.time()
    lines_text = []
    for record in rpred.rpred(net, crop, seg):
        if record.prediction.strip():
            lines_text.append(record.prediction)
    print(f"  reconnaissance en {time.time()-t2:.1f}s", file=sys.stderr)

    print(f"\n─── {name} ({len(lines_text)} lignes) ───")
    for i, t in enumerate(lines_text):
        print(f"  {t}")
