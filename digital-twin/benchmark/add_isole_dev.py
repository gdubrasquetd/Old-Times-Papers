"""Ajoute 2-3 crops 'texte isolé' des 2 unes de DEV au banc (crops/ + manifest.json)."""
import json
from pathlib import Path
from PIL import Image
from ultralytics import YOLO

ROOT = Path(r"C:/Users/antwi/Projets_informatiques/Oldspapers")
WEIGHTS = ROOT / "bloc_detection/runs/multiclass_yolo11s_v3/weights/best.pt"
IMG_DIR = ROOT / "annotation_tool/data/images"
OUT = ROOT / "OCR/bench/crops"
MAN = ROOT / "OCR/bench/manifest.json"
DEV = ["le_figaro_1937-03-22", "intransigeant_1909-03-26"]
PAD = 6

model = YOLO(str(WEIGHTS)); names = model.names
manifest = json.loads(MAN.read_text(encoding="utf-8"))
existing = {m["file"] for m in manifest}
added = []
for slug in DEV:
    src = IMG_DIR / f"{slug}.jpg"
    im = Image.open(src).convert("RGB"); W, H = im.size
    res = model.predict(str(src), conf=0.30, imgsz=1280, verbose=False)[0]
    cand = []
    for b in res.boxes:
        if names[int(b.cls)] != "texte isolé":
            continue
        x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
        h = y1 - y0
        if 200 <= h <= 1600 and (x1 - x0) >= 150:
            cand.append((float(b.conf), x0, y0, x1, y1, h))
    cand.sort(key=lambda t: -t[5])          # plus grands d'abord
    for i, (conf, x0, y0, x1, y1, h) in enumerate(cand[:2]):   # 2 par une
        cx0 = max(0, int(x0 - PAD)); cy0 = max(0, int(y0 - PAD))
        cx1 = min(W, int(x1 + PAD)); cy1 = min(H, int(y1 + PAD))
        fname = f"{slug}__ti{i}__texte_isolé.png"
        im.crop((cx0, cy0, cx1, cy1)).save(OUT / fname)
        if fname not in existing:
            manifest.append({"file": fname, "image": slug, "class": "texte isolé",
                             "conf": round(conf, 2), "box": [cx0, cy0, cx1, cy1],
                             "w": cx1 - cx0, "h": cy1 - cy0})
        added.append((fname, cy1 - cy0))

MAN.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{len(added)} texte isolé ajoutés au dev :")
for f, h in added:
    print(f"  {h}px  {f}")
