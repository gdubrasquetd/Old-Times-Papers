"""
Test Surya OCR sur la même image que PaddleOCR pour comparer la qualité.
Affiche les 30 premières lignes détectées.
Usage : conda run -n oldspapers python OCR/test_surya_ocr.py
"""
import pathlib, sys, json, time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

IMG_PATH = pathlib.Path(__file__).parent.parent / "cache" / "ocr_img" / "bpt6k412758h.jpg"

if not IMG_PATH.exists():
    print("Image introuvable — lance d'abord verify_paddle.py", file=sys.stderr)
    sys.exit(1)

from PIL import Image
img = Image.open(IMG_PATH).convert("RGB")
print(f"Image : {img.size[0]}×{img.size[1]}", file=sys.stderr)

print("Chargement modèles Surya...", file=sys.stderr)
t0 = time.time()

from surya.ocr import run_ocr
from surya.model.detection.model import load_model as load_det_model, load_processor as load_det_processor
from surya.model.recognition.model import load_model as load_rec_model
from surya.model.recognition.processor import load_processor as load_rec_processor

det_model     = load_det_model()
det_processor = load_det_processor()
rec_model     = load_rec_model()
rec_processor = load_rec_processor()

print(f"  modèles chargés en {time.time()-t0:.1f}s", file=sys.stderr)

print("OCR en cours...", file=sys.stderr)
t1 = time.time()
predictions = run_ocr([img], [["fr"]], det_model, det_processor, rec_model, rec_processor)
print(f"  OCR en {time.time()-t1:.1f}s", file=sys.stderr)

result = predictions[0]
lines  = result.text_lines
print(f"\n=== {len(lines)} lignes détectées ===", file=sys.stderr)

for i, line in enumerate(lines[:40]):
    bbox = line.bbox          # [x0, y0, x1, y1]
    conf = round(line.confidence, 3)
    x0n  = round(bbox[0] / img.size[0], 3)
    y0n  = round(bbox[1] / img.size[1], 3)
    print(f"  [{i:2d}] conf={conf}  y={y0n}  '{line.text[:80]}'", file=sys.stderr)

print(f"\n... ({len(lines)} lignes au total)", file=sys.stderr)

# Sortie JSON pour comparaison
out = []
for line in lines:
    out.append({
        "text": line.text,
        "conf": round(line.confidence, 3),
        "x0": round(line.bbox[0] / img.size[0], 4),
        "y0": round(line.bbox[1] / img.size[1], 4),
        "x1": round(line.bbox[2] / img.size[0], 4),
        "y1": round(line.bbox[3] / img.size[1], 4),
    })

out_path = pathlib.Path(__file__).parent.parent / "cache" / "verify" / "surya_lines.json"
out_path.write_text(json.dumps({"lines": out, "n": len(out)}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\nSauvegardé : {out_path.name}", file=sys.stderr)
