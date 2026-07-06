"""Étape 1 de la pipeline complète : détecte les blocs typés d'une une.
Env : bloc_detection (ultralytics).
    python twin_detect.py <slug|image.jpg> <out_blocks.json>
Sortie : blocks.json {slug, img_w, img_h, blocks:[{id,class,conf,box(px),nbox(0-1),text:null}]}
"""
import json, sys
from pathlib import Path
from PIL import Image
from ultralytics import YOLO

ROOT = Path(r"C:/Users/antwi/Projets_informatiques/Oldspapers")
WEIGHTS = ROOT / "bloc_detection/runs/multiclass_yolo11s_v3/weights/best.pt"
IMG_DIR = ROOT / "annotation_tool/data/images"

arg = sys.argv[1]
out = Path(sys.argv[2])
src = Path(arg) if arg.endswith((".jpg", ".png")) else IMG_DIR / f"{arg}.jpg"
slug = src.stem

im = Image.open(src).convert("RGB")
W, H = im.size
model = YOLO(str(WEIGHTS))
names = model.names
res = model.predict(str(src), conf=0.30, imgsz=1280, verbose=False)[0]

blocks = []
for b in res.boxes:
    cls = names[int(b.cls)]
    x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
    blocks.append({"class": cls, "conf": round(float(b.conf), 3),
                   "box": [int(x0), int(y0), int(x1), int(y1)],
                   "nbox": [round(x0 / W, 5), round(y0 / H, 5), round(x1 / W, 5), round(y1 / H, 5)],
                   "text": None})
blocks.sort(key=lambda d: (d["box"][1], d["box"][0]))   # ordre de lecture
for i, d in enumerate(blocks):
    d["id"] = i

out.write_text(json.dumps({"slug": slug, "image": str(src), "img_w": W, "img_h": H,
                           "blocks": blocks}, ensure_ascii=False, indent=2), encoding="utf-8")
from collections import Counter
print(f"{slug}: {len(blocks)} blocs -> {out.name}  {dict(Counter(b['class'] for b in blocks))}")
