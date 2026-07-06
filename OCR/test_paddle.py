"""Test PaddleOCR 2.7 layout + OCR sur une image IIIF Gallica."""
import sys, json, urllib.request, pathlib

ARK = "bpt6k412758h"  # La Croix 1930-05-27
IMG_PATH = pathlib.Path(__file__).parent.parent / "cache" / "ocr_img" / f"{ARK}.jpg"

if not IMG_PATH.exists():
    IMG_PATH.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://gallica.bnf.fr/iiif/ark:/12148/{ARK}/f1/full/1500,/0/native.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        IMG_PATH.write_bytes(r.read())
    print("Image téléchargée", file=sys.stderr)

from PIL import Image
img = Image.open(IMG_PATH)
img_w, img_h = img.size
print(f"Image : {img_w}x{img_h}", file=sys.stderr)

from paddleocr import PaddleOCR
print("Initialisation PaddleOCR 2.7 (det+rec, lang=fr)...", file=sys.stderr)

# PaddleOCR 2.7.x API
ocr = PaddleOCR(
    use_angle_cls=False,
    lang="fr",
    use_gpu=False,
    show_log=False,
    # Seuils plus bas pour attraper le petit texte des journaux anciens
    det_db_thresh=0.2,
    det_db_box_thresh=0.3,
    det_db_unclip_ratio=2.0,
    det_limit_side_len=2048,
    det_limit_type="max",
)

print("Analyse en cours...", file=sys.stderr)
result = ocr.ocr(str(IMG_PATH), cls=False)

if result and result[0]:
    lines = result[0]
    print(f"\n=== {len(lines)} lignes OCR détectées ===", file=sys.stderr)
    for i, line in enumerate(lines[:20]):
        box, (text, conf) = line
        x0 = min(p[0] for p in box) / img_w
        y0 = min(p[1] for p in box) / img_h
        x1 = max(p[0] for p in box) / img_w
        y1 = max(p[1] for p in box) / img_h
        print(f"  [{i:2d}] conf={conf:.2f}  x={x0:.3f}-{x1:.3f}  y={y0:.3f}-{y1:.3f}  '{text[:60]}'",
              file=sys.stderr)
    print(f"  ... ({len(lines)} lignes au total)", file=sys.stderr)

    # Sortie JSON
    out_lines = []
    for line in lines:
        box, (text, conf) = line
        out_lines.append({
            "text": text,
            "conf": round(conf, 3),
            "x0": round(min(p[0] for p in box) / img_w, 4),
            "y0": round(min(p[1] for p in box) / img_h, 4),
            "x1": round(max(p[0] for p in box) / img_w, 4),
            "y1": round(max(p[1] for p in box) / img_h, 4),
        })
    print(json.dumps({"lines": out_lines, "img_w": img_w, "img_h": img_h}))
else:
    print("Aucun résultat", file=sys.stderr)
    print(json.dumps({"lines": [], "img_w": img_w, "img_h": img_h}))
