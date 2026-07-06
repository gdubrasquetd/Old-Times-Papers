"""Test rapide de layoutparser sur une image IIIF Gallica."""
import sys, json, urllib.request, io, pathlib

# Monkeypatch torch.load avant tout import de layoutparser/timm/effdet :
# torch >= 2.6 utilise weights_only=True par défaut, ce qui bloque les anciens
# checkpoints qui utilisent des objets pickle non-allowlistés.
import torch as _torch
_orig_torch_load = _torch.load
_torch.load = lambda *a, **kw: _orig_torch_load(*a, **{**kw, "weights_only": False})

ARK = "bpt6k412758h"  # La Croix 1930-05-27
IMG_PATH = pathlib.Path(__file__).parent.parent / "cache" / "ocr_img" / f"{ARK}.jpg"

# Télécharge l'image si pas en cache
if not IMG_PATH.exists():
    IMG_PATH.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://gallica.bnf.fr/iiif/ark:/12148/{ARK}/f1/full/1500,/0/native.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        IMG_PATH.write_bytes(r.read())
    print(f"Image téléchargée : {IMG_PATH}", file=sys.stderr)

from PIL import Image
img = Image.open(IMG_PATH).convert("RGB")
img_w, img_h = img.size
print(f"Image : {img_w}x{img_h}", file=sys.stderr)

import layoutparser as lp
from layoutparser.models.effdet.layoutmodel import EfficientDetLayoutModel
from layoutparser.models.effdet.catalog import LABEL_MAP_CATALOG
import numpy as np

# Télécharge le modèle si besoin (contourne le bug iopath/Windows sur '?' dans les noms de fichiers)
MODEL_DIR = pathlib.Path.home() / ".torch" / "lp_models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
MODEL_FILE = MODEL_DIR / "publaynet-tf_efficientdet_d1.pth.tar"
MODEL_URL = "https://www.dropbox.com/s/gxy11xkkiwnpgog/publaynet-tf_efficientdet_d1.pth.tar?dl=1"

if not MODEL_FILE.exists():
    print(f"Téléchargement du modèle PubLayNet d1...", file=sys.stderr)
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(MODEL_FILE, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    print(f"Modèle sauvé : {MODEL_FILE}", file=sys.stderr)

print("Chargement modèle PubLayNet tf_efficientdet_d1...", file=sys.stderr)
model = EfficientDetLayoutModel(
    "lp://efficientdet/PubLayNet/tf_efficientdet_d1",
    model_path=str(MODEL_FILE),
    label_map=LABEL_MAP_CATALOG["PubLayNet"],
)

img_np = np.array(img)
print("Détection en cours...", file=sys.stderr)
layout = model.detect(img_np)

blocks = []
for i, block in enumerate(layout):
    x0, y0, x1, y1 = block.block.x_1, block.block.y_1, block.block.x_2, block.block.y_2
    blocks.append({
        "position": i,
        "label": block.type,
        "score": round(float(block.score), 3),
        "x0": round(x0 / img_w, 4),
        "y0": round(y0 / img_h, 4),
        "x1": round(x1 / img_w, 4),
        "y1": round(y1 / img_h, 4),
    })

print(f"\n=== {len(blocks)} blocs détectés ===", file=sys.stderr)
for b in blocks:
    print(f"  [{b['position']:2d}] {b['label']:8s} score={b['score']:.2f}  "
          f"x0={b['x0']:.3f} x1={b['x1']:.3f}  y0={b['y0']:.3f} y1={b['y1']:.3f}  "
          f"w={b['x1']-b['x0']:.3f}", file=sys.stderr)

# Sortie JSON sur stdout
print(json.dumps({"blocks": blocks, "img_w": img_w, "img_h": img_h, "n": len(blocks)}))
