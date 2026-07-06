"""Exécute la CHAÎNE FIGÉE (routage calibré sur le dev) sur un jeu de crops.
Politique (NE PAS re-tuner) :
  - 'bloc de texte' / 'texte isolé' -> Kraken (segmentation blla)
  - 'header'                        -> Kraken mode 'whole' (une seule ligne)
  - 'titre'                         -> doctr

Lancé en 2 étapes (envs différents) :
  oldspapers : python pipeline_run.py --stage kraken
  ocr_torch  : python pipeline_run.py --stage doctr
Résultats fusionnés dans results_test.json.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
CROPS = HERE / "crops_test"
MAN = HERE / "manifest_test40.json"
RES = HERE / "results_test.json"

ROUTE = {"bloc de texte": "kraken", "texte isolé": "doctr",
         "header": "kraken_whole", "titre": "doctr"}


def run_kraken(items):
    import kraken, os
    from kraken.lib import models as kmodels
    from kraken.lib.vgsl import TorchVGSLModel
    from kraken import rpred, blla
    from kraken.containers import Segmentation, BBoxLine
    REC = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
           r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
    net = kmodels.load_any(REC)
    seg_model = TorchVGSLModel.load_model(os.path.join(os.path.dirname(kraken.__file__), "blla.mlmodel"))
    out = {}
    for f, route in items:
        im = Image.open(CROPS / f).convert("RGB")
        try:
            if route == "kraken_whole":
                W, Hh = im.size
                seg = Segmentation(type="bbox", imagename="", text_direction="horizontal-lr",
                                   script_detection=False, regions={}, line_orders=[],
                                   lines=[BBoxLine(id="l0", bbox=(0, 0, W, Hh), text=None)])
                txt = ""
                for rec in rpred.rpred(net, im, seg):
                    txt = rec.prediction or ""
                    break
            else:
                seg = blla.segment(im, model=seg_model)
                txt = "\n".join(r.prediction for r in rpred.rpred(net, im, seg) if r.prediction)
        except Exception as e:
            print(f"  KO {f}: {e}", flush=True); txt = ""
        out[f] = {"engine": route, "text": txt}
        print(f"  {route:12} {f}", flush=True)
    return out


def run_doctr(items):
    from doctr.models import ocr_predictor
    import numpy as np
    model = ocr_predictor(pretrained=True)
    out = {}
    for f, route in items:
        im = Image.open(CROPS / f).convert("RGB")
        res = model([np.array(im)])
        out[f] = {"engine": "doctr", "text": res.render()}
        print(f"  doctr        {f}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["kraken", "doctr"])
    args = ap.parse_args()
    man = json.loads(MAN.read_text(encoding="utf-8"))
    results = json.loads(RES.read_text(encoding="utf-8")) if RES.exists() else {}

    if args.stage == "kraken":
        items = [(m["file"], ROUTE[m["class"]]) for m in man if ROUTE[m["class"]].startswith("kraken")]
        out = run_kraken(items)
    else:
        items = [(m["file"], "doctr") for m in man if ROUTE[m["class"]] == "doctr"]
        out = run_doctr(items)

    results.update(out)
    RES.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(out)} blocs traités ({args.stage}) -> {RES}")


if __name__ == "__main__":
    main()
