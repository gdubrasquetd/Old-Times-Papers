"""Étape 1 du banc d'essai OCR : détecte les blocs avec notre modèle multi-classes
et sauve les crops des blocs de TEXTE (pour les OCRiser ensuite avec chaque moteur).

Env : bloc_detection (ultralytics).
Sortie : OCR/bench/crops/<slug>__<i>__<classe>.png + manifest.json
"""
from __future__ import annotations
import json
from pathlib import Path
from PIL import Image
from ultralytics import YOLO

ROOT = Path(r"C:/Users/antwi/Projets_informatiques/Oldspapers")
WEIGHTS = ROOT / "bloc_detection/runs/multiclass_yolo11s_v3/weights/best.pt"
IMG_DIR = ROOT / "annotation_tool/data/images"
OUT = ROOT / "OCR/bench/crops"
OUT.mkdir(parents=True, exist_ok=True)

TEST_IMAGES = ["le_figaro_1937-03-22", "intransigeant_1909-03-26"]
TEXT_CLASSES = {"header", "titre", "bloc de texte", "texte isolé"}
PAD = 6            # px de marge autour du bloc
MAX_PER_IMG = 10  # blocs (les plus grands) par une, pour rester maniable

model = YOLO(str(WEIGHTS))
names = model.names
manifest = []

for slug in TEST_IMAGES:
    src = IMG_DIR / f"{slug}.jpg"
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
    blocks.sort(key=lambda t: -t[6])          # plus grands d'abord
    blocks = blocks[:MAX_PER_IMG]
    blocks.sort(key=lambda t: (t[3], t[2]))   # ordre de lecture (haut->bas)

    for i, (cls, conf, x0, y0, x1, y1, _) in enumerate(blocks):
        cx0 = max(0, int(x0 - PAD)); cy0 = max(0, int(y0 - PAD))
        cx1 = min(W, int(x1 + PAD)); cy1 = min(H, int(y1 + PAD))
        crop = im.crop((cx0, cy0, cx1, cy1))
        cslug = cls.replace(" ", "_")
        fname = f"{slug}__{i:02d}__{cslug}.png"
        crop.save(OUT / fname)
        manifest.append({"file": fname, "image": slug, "class": cls,
                         "conf": round(conf, 2), "box": [cx0, cy0, cx1, cy1],
                         "w": cx1 - cx0, "h": cy1 - cy0})
    print(f"{slug} : {len(blocks)} blocs texte")

(OUT.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
print(f"\n{len(manifest)} crops écrits -> {OUT}")
