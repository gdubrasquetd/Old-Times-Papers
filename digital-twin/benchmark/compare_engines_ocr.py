"""OCRise TOUS les blocs de texte (dev+test) avec UN moteur -> {file: text}.
    oldspapers : python compare_engines_ocr.py --engine kraken --out kraken_all.json
    ocr_torch  : python compare_engines_ocr.py --engine doctr  --out doctr_all.json
"""
import argparse, json
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
T_TEST = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")
TEXT = {"bloc de texte", "titre", "texte isolé"}


def blocklist():
    """(file, crop_path, class) pour tous les blocs de texte dev+test."""
    items = []
    mand = {m["file"]: m for m in json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))}
    for f, m in mand.items():
        if m["class"] in TEXT and (HERE / "crops" / f).exists():
            items.append((f, HERE / "crops" / f, m["class"]))
    mant = {m["file"]: m for m in json.loads((HERE / "manifest_test40.json").read_text(encoding="utf-8"))}
    for p in T_TEST.glob("*.txt"):
        f = p.stem + ".png"
        if f in mant and mant[f]["class"] in TEXT and (HERE / "crops_test" / f).exists():
            items.append((f, HERE / "crops_test" / f, mant[f]["class"]))
    return items


def kraken_engine(items):
    import kraken, os
    from kraken.lib import models as kmodels
    from kraken.lib.vgsl import TorchVGSLModel
    from kraken import rpred, blla
    REC = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
           r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
    net = kmodels.load_any(REC)
    seg = TorchVGSLModel.load_model(os.path.join(os.path.dirname(kraken.__file__), "blla.mlmodel"))
    out = {}
    for i, (f, path, cls) in enumerate(items):
        im = Image.open(path).convert("RGB")
        try:
            s = blla.segment(im, model=seg)
            out[f] = "\n".join(r.prediction for r in rpred.rpred(net, im, s) if r.prediction)
        except Exception as e:
            out[f] = ""; print(f"  KO {f}: {e}", flush=True)
        print(f"  [{i+1}/{len(items)}] {f}", flush=True)
    return out


def doctr_engine(items):
    from doctr.models import ocr_predictor
    import numpy as np
    model = ocr_predictor(pretrained=True)
    out = {}
    for i, (f, path, cls) in enumerate(items):
        out[f] = model([np.array(Image.open(path).convert("RGB"))]).render()
        print(f"  [{i+1}/{len(items)}] {f}", flush=True)
    return out


ap = argparse.ArgumentParser()
ap.add_argument("--engine", required=True, choices=["kraken", "doctr"])
ap.add_argument("--out", required=True)
a = ap.parse_args()
items = blocklist()
print(f"{a.engine}: {len(items)} blocs de texte", flush=True)
res = (kraken_engine if a.engine == "kraken" else doctr_engine)(items)
(HERE / a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> {a.out}")
