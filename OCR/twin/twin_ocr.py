"""Étape 2 : OCRise les blocs de texte et écrit le texte dans blocks.json.
Politique figée (cf. RECAP_OCR.md) : PERO-OCR (modèle presse européenne) pour TOUT
le texte — corps, titres, texte isolé (il fait sa propre détection de lignes robuste,
meilleur partout : ~4% WER). 'header' -> nom du journal (pas d'OCR) ; 'illustration' -> ignoré.

    pero : python twin_ocr.py <blocks.json> --stage pero
(kraken/doctr restent dispo en secours, dans leurs envs respectifs.)
"""
import argparse, json, re
from pathlib import Path
from PIL import Image

TEXT = {"bloc de texte", "titre", "texte isolé"}
ROUTE = {c: "pero" for c in TEXT}         # PERO pour tout le texte
PERO_MODEL = (Path(__file__).resolve().parent.parent
              / "bench" / "comp" / "models" / "pero" / "pero_eu_cz_print_newspapers_2022-09-26")
REC_MODEL = (r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
             r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel")
PAD = 6


def dehyphenate(s):
    """Recolle les mots coupés en fin de ligne ('auto-\\nrité' -> 'autorité'). Post-
    traitement clé (WER -5%). Les tirets réels (composés) sont en milieu de ligne -> gardés."""
    return re.sub(r"([A-Za-zÀ-ÿ])[¬­\-]\s*\n\s*([a-zà-ÿ])", r"\1\2", s or "")


def crop_of(im, b):
    x0, y0, x1, y1 = b["box"]
    W, H = im.size
    return im.crop((max(0, x0 - PAD), max(0, y0 - PAD), min(W, x1 + PAD), min(H, y1 + PAD)))


def tesseract_fallback(crop):
    """Filet de secours quand PERO ne détecte rien (ex. gros titres display) :
    Tesseract lit le crop directement. Binaire système, appelé en sous-processus."""
    import shutil, subprocess, tempfile
    tess = shutil.which("tesseract") or r"C:/Program Files/Tesseract-OCR/tesseract.exe"
    tmp = Path(tempfile.gettempdir()) / "twin_fb.png"
    crop.save(tmp)
    r = subprocess.run([tess, str(tmp), "stdout", "-l", "fra", "--psm", "6"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip()


def run_pero(im, blocks):
    import configparser
    import numpy as np, torch
    from pero_ocr.document_ocr.page_parser import PageParser
    from pero_ocr.core.layout import PageLayout
    gpu = torch.cuda.is_available()
    print(f"  PERO sur {'GPU (' + torch.cuda.get_device_name(0) + ')' if gpu else 'CPU'}", flush=True)
    cfg = configparser.ConfigParser()
    cfg.read(str(PERO_MODEL / ("config.ini" if gpu else "config_cpu.ini")))
    parser = PageParser(cfg, config_path=str(PERO_MODEL))
    for b in blocks:
        crop = crop_of(im, b)
        arr = np.array(crop.convert("RGB"))[:, :, ::-1]   # RGB -> BGR
        try:
            pl = PageLayout(id="b", page_size=(arr.shape[0], arr.shape[1]))
            pl = parser.process_page(arr, pl)
            lines = [ln.transcription for reg in pl.regions for ln in reg.lines if ln.transcription]
            txt = dehyphenate("\n".join(lines))
        except Exception as e:
            print(f"  pero KO bloc {b['id']}: {e}", flush=True); txt = ""
        b["engine"] = "pero"
        if not txt.strip():                       # PERO n'a rien lu -> fallback Tesseract
            fb = tesseract_fallback(crop)
            if fb.strip():
                txt = dehyphenate(fb); b["engine"] = "pero+tesseract"
        b["text"] = txt
        print(f"  bloc {b['id']} ({b['class']}) [{b['engine']}]", flush=True)


def run_kraken(im, blocks):     # secours
    import kraken, os
    from kraken.lib import models as kmodels
    from kraken.lib.vgsl import TorchVGSLModel
    from kraken import rpred, blla
    net = kmodels.load_any(REC_MODEL)
    seg_model = TorchVGSLModel.load_model(os.path.join(os.path.dirname(kraken.__file__), "blla.mlmodel"))
    for b in blocks:
        c = crop_of(im, b)
        try:
            seg = blla.segment(c, model=seg_model)
            b["text"] = dehyphenate("\n".join(r.prediction for r in rpred.rpred(net, c, seg) if r.prediction))
        except Exception as e:
            print(f"  kraken KO bloc {b['id']}: {e}", flush=True); b["text"] = ""
        b["engine"] = "kraken"


def run_doctr(im, blocks):      # secours
    from doctr.models import ocr_predictor
    import numpy as np
    model = ocr_predictor(pretrained=True)
    for b in blocks:
        b["text"] = dehyphenate(model([np.array(crop_of(im, b))]).render())
        b["engine"] = "doctr"


RUNNERS = {"pero": run_pero, "kraken": run_kraken, "doctr": run_doctr}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blocks")
    ap.add_argument("--stage", default="pero", choices=list(RUNNERS))
    args = ap.parse_args()
    p = Path(args.blocks)
    data = json.loads(p.read_text(encoding="utf-8"))
    im = Image.open(data["image"]).convert("RGB")
    todo = [b for b in data["blocks"] if b["class"] in TEXT]
    print(f"{args.stage}: {len(todo)} blocs de texte à OCRiser", flush=True)
    RUNNERS[args.stage](im, todo)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {p.name} mis à jour")


if __name__ == "__main__":
    main()
