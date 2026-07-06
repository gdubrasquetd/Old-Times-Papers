"""Étape 2 du banc d'essai OCR : OCRise chaque crop de bloc avec un ou plusieurs
moteurs et accumule les résultats (JSON + markdown de comparaison).

Env : oldspapers.
    python ocr_bench.py --engines surya
    python ocr_bench.py --engines tesseract
Les résultats sont fusionnés dans results.json (on peut lancer un moteur à la fois).
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
CROPS = HERE / "crops"
MANIFEST = HERE / "manifest.json"
RESULTS = HERE / "results.json"


# ── Moteurs (chacun : liste d'images PIL -> liste de textes) ──

def engine_surya(images):
    # API moderne (RecognitionPredictor/DetectionPredictor) avec repli sur run_ocr.
    try:
        from surya.recognition import RecognitionPredictor
        from surya.detection import DetectionPredictor
        rec, det = RecognitionPredictor(), DetectionPredictor()
        try:
            preds = rec(images, det_predictor=det)
        except TypeError:
            preds = rec(images, [["fr"]] * len(images), det)
    except Exception:
        from surya.ocr import run_ocr
        from surya.model.detection.model import load_model as ld, load_processor as ldp
        from surya.model.recognition.model import load_model as lr
        from surya.model.recognition.processor import load_processor as lrp
        preds = run_ocr(images, [["fr"]] * len(images), ld(), ldp(), lr(), lrp())
    out = []
    for p in preds:
        lines = sorted(p.text_lines, key=lambda l: l.bbox[1])
        out.append("\n".join(l.text for l in lines))
    return out


def engine_doctr(images):
    from doctr.models import ocr_predictor
    import numpy as np
    model = ocr_predictor(pretrained=True)
    out = []
    for im in images:
        res = model([np.array(im)])
        out.append(res.render())
    return out


def engine_paddle(images):
    from paddleocr import PaddleOCR
    import numpy as np
    # Construction robuste aux versions : 3.x refuse show_log/use_angle_cls.
    ocr = None
    for kw in ({"lang": "fr", "use_angle_cls": True, "show_log": False},
               {"lang": "fr", "use_textline_orientation": True},
               {"lang": "fr"}):
        try:
            ocr = PaddleOCR(**kw); break
        except (TypeError, ValueError):
            continue
    if ocr is None:
        ocr = PaddleOCR()

    def texts_from(res):
        """Extrait les textes du résultat, formats 2.x (list[[box,(txt,score)]])
        et 3.x (list[OCRResult dict avec 'rec_texts'])."""
        lines = []
        for r in (res or []):
            # 3.x : dict-like avec rec_texts (parfois sous r['res'])
            d = r.get("res", r) if isinstance(r, dict) else r
            if isinstance(d, dict) and "rec_texts" in d:
                lines.extend(d["rec_texts"]); continue
            # 2.x : r = liste d'items [box, (texte, score)]
            page = r if isinstance(r, list) else [r]
            for item in page:
                try:
                    lines.append(item[1][0] if isinstance(item[1], (list, tuple)) else item[1])
                except Exception:
                    pass
        return lines

    out = []
    for im in images:
        arr = np.array(im)
        try:
            res = ocr.predict(arr)
        except Exception:
            res = ocr.ocr(arr)
        out.append("\n".join(texts_from(res)))
    return out


def engine_tesseract(images):
    import pytesseract, shutil
    for cand in [shutil.which("tesseract"), r"C:/Program Files/Tesseract-OCR/tesseract.exe"]:
        if cand and Path(cand).exists():
            pytesseract.pytesseract.tesseract_cmd = cand
            break
    # --psm 6 : bloc de texte homogène (adapté à un crop de bloc)
    return [pytesseract.image_to_string(im, lang="fra", config="--psm 6") for im in images]


def engine_easyocr(images):
    import easyocr, numpy as np
    reader = easyocr.Reader(["fr"], gpu=True)
    out = []
    for im in images:
        res = reader.readtext(np.array(im), detail=1, paragraph=True)
        res = sorted(res, key=lambda r: r[0][0][1])   # haut -> bas
        out.append("\n".join(r[1] for r in res))
    return out


def engine_kraken(images):
    """Kraken CATMuS-Print (imprimé ancien) : segmentation baseline (blla) puis
    reconnaissance. Modèles chargés une seule fois. Env : oldspapers."""
    import kraken, os
    from kraken.lib import models as kmodels
    from kraken.lib.vgsl import TorchVGSLModel
    from kraken import rpred, blla
    REC_MODEL = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
                 r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
    net = kmodels.load_any(REC_MODEL)
    seg_model = TorchVGSLModel.load_model(
        os.path.join(os.path.dirname(kraken.__file__), "blla.mlmodel"))
    out = []
    for im in images:
        try:
            seg = blla.segment(im, model=seg_model)
            lines = [r.prediction for r in rpred.rpred(net, im, seg)]
            out.append("\n".join(l for l in lines if l))
        except Exception as e:
            print(f"    kraken KO sur un crop : {type(e).__name__}: {e}", flush=True)
            out.append("")
    return out


ENGINES = {"surya": engine_surya, "doctr": engine_doctr, "paddle": engine_paddle,
           "tesseract": engine_tesseract, "easyocr": engine_easyocr,
           "kraken": engine_kraken}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="surya", help="séparés par virgule")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = [m["file"] for m in manifest]
    images = [Image.open(CROPS / f).convert("RGB") for f in files]

    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}
    for f in files:
        results.setdefault(f, {})

    for eng in [e.strip() for e in args.engines.split(",") if e.strip()]:
        if eng not in ENGINES:
            print(f"moteur inconnu : {eng}"); continue
        print(f"=== {eng} : OCR de {len(images)} crops… ===", flush=True)
        t0 = time.time()
        texts = ENGINES[eng](images)
        dt = time.time() - t0
        for f, txt in zip(files, texts):
            results[f][eng] = {"text": txt, "secs": round(dt / len(images), 2)}
        print(f"    {eng} fait en {dt:.0f}s ({dt/len(images):.1f}s/crop)", flush=True)

    RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRésultats -> {RESULTS}")


if __name__ == "__main__":
    main()
