"""Worker standalone PaddleOCR — lancé en sous-processus par ocr_local.py.

Usage : python paddle_worker.py <image_path>
Sortie : JSON sur stdout {lines: [{text, conf, x0, y0, x1, y1, w, h, xc}...]}
Erreur : JSON sur stdout {error: str}
"""
import json
import sys
import pathlib

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: paddle_worker.py <image_path>"}))
        sys.exit(1)

    img_path = pathlib.Path(sys.argv[1])
    if not img_path.exists():
        print(json.dumps({"error": f"Image introuvable : {img_path}"}))
        sys.exit(1)

    try:
        from PIL import Image
        img = Image.open(img_path)
        img_w, img_h = img.size
    except Exception as e:
        print(json.dumps({"error": f"Lecture image : {e}"}))
        sys.exit(1)

    try:
        from paddleocr import PaddleOCR
        ocr = PaddleOCR(
            use_angle_cls=False,
            lang="fr",
            use_gpu=False,
            show_log=False,
            det_db_thresh=0.2,
            det_db_box_thresh=0.3,
            det_db_unclip_ratio=2.0,
            det_limit_side_len=2048,
            det_limit_type="max",
        )
    except Exception as e:
        print(json.dumps({"error": f"Initialisation PaddleOCR : {e}"}))
        sys.exit(1)

    try:
        raw = ocr.ocr(str(img_path), cls=False)
    except Exception as e:
        print(json.dumps({"error": f"OCR : {e}"}))
        sys.exit(1)

    if not raw or not raw[0]:
        print(json.dumps({"error": "Aucune ligne détectée"}))
        sys.exit(1)

    lines = []
    for item in raw[0]:
        box, (text, conf) = item
        if conf < 0.4 or not text.strip():
            continue
        px0 = min(p[0] for p in box)
        py0 = min(p[1] for p in box)
        px1 = max(p[0] for p in box)
        py1 = max(p[1] for p in box)
        w = px1 - px0
        h = py1 - py0
        if w <= 0 or h <= 0:
            continue
        lines.append({
            "text": text.strip(),
            "conf": round(conf, 3),
            "x0": round(px0, 1), "y0": round(py0, 1),
            "x1": round(px1, 1), "y1": round(py1, 1),
            "w": round(w, 1), "h": round(h, 1),
            "xc": round((px0 + px1) / 2, 1),
        })

    print(json.dumps({"lines": lines, "img_w": img_w, "img_h": img_h},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
