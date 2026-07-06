"""Runner générique : OCRise le jeu de blocs avec un moteur+config -> res_<label>.json.
    oldspapers     : python run_engine.py --engine kraken_lines
    bloc_detection : python run_engine.py --engine easyocr_para
    bloc_detection : python run_engine.py --engine easyocr_nopara
"""
import argparse, json, sys
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_lib import blocklist

HERE = Path(__file__).resolve().parent


def kraken_lines(items):
    import cv2, numpy as np
    from kraken.lib import models as kmodels
    from kraken import rpred
    from kraken.containers import Segmentation, BBoxLine
    REC = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
           r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
    net = kmodels.load_any(REC)

    def split(gray):
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

    out = {}
    for k_, (f, path, cls, gt) in enumerate(items):
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        lines = []
        for (y0, y1) in split(gray):
            pad = int((y1 - y0) * 0.12)
            crop = gray[max(0, y0 - pad):min(gray.shape[0], y1 + pad), :]
            pil = Image.fromarray(crop).convert("RGB")
            seg = Segmentation(type="bbox", imagename="", text_direction="horizontal-lr",
                               script_detection=False, regions={}, line_orders=[],
                               lines=[BBoxLine(id="l0", bbox=(0, 0, pil.width, pil.height), text=None)])
            try:
                for rec in rpred.rpred(net, pil, seg):
                    lines.append(rec.prediction or ""); break
            except Exception:
                lines.append("")
        out[f] = "\n".join(lines)
        print(f"  [{k_+1}/{len(items)}] {f}", flush=True)
    return out


def easyocr_engine(items, paragraph):
    import easyocr, numpy as np
    reader = easyocr.Reader(["fr"], gpu=True)
    out = {}
    for k_, (f, path, cls, gt) in enumerate(items):
        res = reader.readtext(np.array(Image.open(path).convert("RGB")), detail=1, paragraph=paragraph)
        res = sorted(res, key=lambda r: r[0][0][1])
        out[f] = "\n".join((r[1] if paragraph else r[1]) for r in res)
        print(f"  [{k_+1}/{len(items)}] {f}", flush=True)
    return out


ap = argparse.ArgumentParser()
ap.add_argument("--engine", required=True)
a = ap.parse_args()
items = blocklist()
print(f"{a.engine}: {len(items)} blocs", flush=True)
if a.engine == "kraken_lines":
    out = kraken_lines(items)
elif a.engine == "easyocr_para":
    out = easyocr_engine(items, True)
elif a.engine == "easyocr_nopara":
    out = easyocr_engine(items, False)
else:
    sys.exit("moteur inconnu")
(HERE / f"res_{a.engine}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"-> res_{a.engine}.json")
