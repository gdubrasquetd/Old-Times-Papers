"""PERO-OCR (modèle presse européenne) sur le jeu de blocs -> res_pero.json.
Pipeline complet PERO : détection lignes/régions -> crop -> OCR. Env : pero.
"""
import configparser, json, os, sys
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_lib import blocklist

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "pero" / "pero_eu_cz_print_newspapers_2022-09-26"

from pero_ocr.document_ocr.page_parser import PageParser
from pero_ocr.core.layout import PageLayout

def ocr_image(path, parser):
    img = np.array(Image.open(path).convert("RGB"))[:, :, ::-1]  # RGB->BGR, unicode-safe
    pl = PageLayout(id="b", page_size=(img.shape[0], img.shape[1]))
    pl = parser.process_page(img, pl)
    lines = []
    for reg in pl.regions:
        for ln in reg.lines:
            if ln.transcription:
                lines.append(ln.transcription)
    return "\n".join(lines)


def main():
    cfg = configparser.ConfigParser()
    cfg.read(str(MODEL / "config_cpu.ini"))
    parser = PageParser(cfg, config_path=str(MODEL))
    items = blocklist()
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(items)
    print(f"pero: {min(limit, len(items))} blocs", flush=True)
    out = {}
    for i, (f, path, cls, gt) in enumerate(items[:limit]):
        try:
            out[f] = ocr_image(path, parser)
        except Exception as e:
            print(f"  KO {f}: {e}", flush=True); out[f] = ""
        print(f"  [{i+1}] {f}", flush=True)
    (HERE / "res_pero.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-> res_pero.json")


if __name__ == "__main__":
    main()
