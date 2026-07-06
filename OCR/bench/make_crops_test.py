"""Jeu de TEST (généralisation) : détecte les blocs sur des unes DIVERSES, jamais
utilisées pour calibrer le routage. Sort crops_test/ + manifest_test.json.
Env : bloc_detection (ultralytics). python make_crops_test.py
"""
from __future__ import annotations
import json
from pathlib import Path
from PIL import Image
from ultralytics import YOLO

ROOT = Path(r"C:/Users/antwi/Projets_informatiques/Oldspapers")
WEIGHTS = ROOT / "bloc_detection/runs/multiclass_yolo11s_v3/weights/best.pt"
IMG_DIR = ROOT / "annotation_tool/data/images"
OUT = ROOT / "OCR/bench/crops_test"
OUT.mkdir(parents=True, exist_ok=True)

# 8 unes variées (journaux, courants, décennies différentes ; hors jeu de dev)
TEST_IMAGES = [
    "petit_journal_1902-04-25", "humanite_1906-05-16", "le_matin_1910-09-13",
    "le_journal_1914-10-01", "journal_des_debats_1919-08-27",
    "action_francaise_1926-08-21", "le_temps_1934-03-24", "petit_parisien_1935-05-15",
]
TEXT_CLASSES = {"header", "titre", "bloc de texte", "texte isolé"}
PAD = 6

model = YOLO(str(WEIGHTS))
names = model.names
manifest = []

for slug in TEST_IMAGES:
    src = IMG_DIR / f"{slug}.jpg"
    if not src.exists():
        print("manquant:", slug); continue
    im = Image.open(src).convert("RGB")
    W, H = im.size
    res = model.predict(str(src), conf=0.30, imgsz=1280, verbose=False)[0]
    blocks = []
    for b in res.boxes:
        cls = names[int(b.cls)]
        if cls not in TEXT_CLASSES:
            continue
        x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
        blocks.append((cls, float(b.conf), x0, y0, x1, y1, (x1 - x0) * (y1 - y0)))
    blocks.sort(key=lambda t: (t[3], t[2]))   # ordre de lecture
    n = 0
    for i, (cls, conf, x0, y0, x1, y1, area) in enumerate(blocks):
        cx0 = max(0, int(x0 - PAD)); cy0 = max(0, int(y0 - PAD))
        cx1 = min(W, int(x1 + PAD)); cy1 = min(H, int(y1 + PAD))
        if (cx1 - cx0) < 40 or (cy1 - cy0) < 25:
            continue
        crop = im.crop((cx0, cy0, cx1, cy1))
        cslug = cls.replace(" ", "_")
        fname = f"{slug}__{i:02d}__{cslug}.png"
        crop.save(OUT / fname)
        manifest.append({"file": fname, "image": slug, "class": cls,
                         "conf": round(conf, 2), "w": cx1 - cx0, "h": cy1 - cy0})
        n += 1
    print(f"{slug}: {n} blocs")

(OUT.parent / "manifest_test.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n{len(manifest)} crops -> {OUT}")
