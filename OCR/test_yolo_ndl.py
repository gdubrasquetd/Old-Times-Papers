"""Test YOLOv8 NDL Layout sur une image IIIF Gallica."""
import sys, json, urllib.request, pathlib

ARK = "bpt6k412758h"  # La Croix 1930-05-27
IMG_PATH = pathlib.Path(__file__).parent.parent / "cache" / "ocr_img" / f"{ARK}.jpg"

if not IMG_PATH.exists():
    IMG_PATH.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://gallica.bnf.fr/iiif/ark:/12148/{ARK}/f1/full/1500,/0/native.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        IMG_PATH.write_bytes(r.read())
    print(f"Image téléchargée", file=sys.stderr)

from PIL import Image
img = Image.open(IMG_PATH)
img_w, img_h = img.size
print(f"Image : {img_w}x{img_h}", file=sys.stderr)

from ultralytics import YOLO
print("Chargement nakamura196/yolov8-ndl-layout...", file=sys.stderr)
model = YOLO("nakamura196/yolov8-ndl-layout")

print("Détection...", file=sys.stderr)
results = model.predict(str(IMG_PATH), conf=0.25, device="cpu", verbose=False)
r = results[0]

names = r.names
boxes = r.boxes

print(f"\n=== {len(boxes)} blocs détectés ===", file=sys.stderr)
print(f"Classes disponibles : {names}", file=sys.stderr)

blocks = []
for i, box in enumerate(boxes):
    cls_id = int(box.cls[0])
    label = names[cls_id]
    score = float(box.conf[0])
    x0, y0, x1, y1 = box.xyxy[0].tolist()
    blocks.append({
        "position": i,
        "label": label,
        "score": round(score, 3),
        "x0": round(x0 / img_w, 4),
        "y0": round(y0 / img_h, 4),
        "x1": round(x1 / img_w, 4),
        "y1": round(y1 / img_h, 4),
    })

for b in blocks:
    print(f"  [{b['position']:2d}] {b['label']:20s} score={b['score']:.2f}  "
          f"x0={b['x0']:.3f}-{b['x1']:.3f}  y0={b['y0']:.3f}-{b['y1']:.3f}  "
          f"w={b['x1']-b['x0']:.3f}", file=sys.stderr)

print(json.dumps({"blocks": blocks, "img_w": img_w, "img_h": img_h}))
