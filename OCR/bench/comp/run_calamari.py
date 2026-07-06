"""Calamari (modèle historical_french) sur le jeu de blocs -> res_calamari.json.
Recognizer ligne par ligne -> on découpe les lignes par projection. Env : calamari.
"""
import json, os, sys
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
from pathlib import Path
import numpy as np, cv2
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_lib import blocklist

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "calamari_repo" / "historical_french"


def split_lines(gray):
    g = cv2.medianBlur(gray, 3)
    _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rows = (bw > 0).sum(axis=1).astype(float); H = len(rows)
    if rows.max() == 0:
        return [(0, H)]
    k = max(3, H // 300) | 1
    rows = np.convolve(rows, np.ones(k) / k, mode="same")
    on = rows > rows.max() * 0.10
    bands, i = [], 0
    while i < H:
        if on[i]:
            j = i
            while j < H and on[j]:
                j += 1
            if j - i >= max(6, H // 120):
                bands.append((i, j))
            i = j
        else:
            i += 1
    return bands or [(0, H)]


def main():
    from calamari_ocr.ocr.predict.predictor import Predictor, PredictorParams
    predictor = Predictor.from_checkpoint(PredictorParams(), checkpoint=str(MODEL / "0.ckpt"))
    items = blocklist()
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(items)
    print(f"calamari: {min(limit, len(items))} blocs", flush=True)
    out = {}
    for k, (f, path, cls, gt) in enumerate(items[:limit]):
        gray = np.array(Image.open(path).convert("L"))
        line_imgs = []
        for (y0, y1) in split_lines(gray):
            pad = int((y1 - y0) * 0.12)
            line_imgs.append(gray[max(0, y0 - pad):min(gray.shape[0], y1 + pad), :])
        try:
            preds = list(predictor.predict_raw(line_imgs))
            texts = [getattr(p.outputs, "sentence", "") for p in preds]
            out[f] = "\n".join(texts)
        except Exception as e:
            print(f"  KO {f}: {e}", flush=True); out[f] = ""
        print(f"  [{k+1}] {f}", flush=True)
    (HERE / "res_calamari.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-> res_calamari.json")


if __name__ == "__main__":
    main()
