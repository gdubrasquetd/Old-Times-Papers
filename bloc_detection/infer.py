"""
Inférence d'un détecteur de blocs sur une une, avec visualisation.

    python infer.py --weights runs/yolo11s/weights/best.pt --image chemin/une.jpg
    python infer.py --weights runs/rtdetr-l/weights/best.pt --image une.jpg --conf 0.4

Sauve une image annotée à côté de la source (suffixe _pred) et imprime les boîtes
détectées (classe, confiance, bbox en pixels). Base de la Phase 2 (annotation
assistée) et de la Phase 4 (découpe en blocs avant OCR).
"""
from __future__ import annotations
import argparse
from pathlib import Path


def load_model(weights: str):
    from ultralytics import YOLO, RTDETR
    Model = RTDETR if "rtdetr" in Path(weights).as_posix().lower() else YOLO
    return Model(weights)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=0)
    ap.add_argument("--out", default=None, help="chemin image annotée (défaut: <image>_pred.jpg)")
    args = ap.parse_args()

    src = Path(args.image)
    if not src.exists():
        raise SystemExit(f"Image introuvable : {src}")

    model = load_model(args.weights)
    res = model.predict(source=str(src), conf=args.conf, imgsz=args.imgsz,
                        device=args.device, verbose=False)[0]

    names = res.names
    print(f"\n{len(res.boxes)} blocs détectés sur {src.name} :")
    for b in res.boxes:
        cls = names[int(b.cls)]
        conf = float(b.conf)
        x0, y0, x1, y1 = (int(v) for v in b.xyxy[0])
        print(f"  {cls:<16} {conf:.2f}  [{x0},{y0},{x1},{y1}]")

    out = Path(args.out) if args.out else src.with_name(f"{src.stem}_pred.jpg")
    res.save(filename=str(out))
    print(f"\nVisualisation -> {out}")


if __name__ == "__main__":
    main()
