"""
Entraînement / fine-tuning d'un détecteur de blocs (Ultralytics).

Même script pour YOLO11 et RT-DETR : seul --model change. Réglages par défaut
prudents pour une RTX 3050 Laptop (4 Go VRAM) : modèle léger, batch petit, AMP.

NOTE RT-DETR : il impose imgsz=1280 (ignore --imgsz) et consomme ~3,2 Go en
batch 1 sur la 3050 -> garder --batch 1. YOLO11s à 1280 reste très à l'aise.

Exemples :
    python train.py --model yolo11s --imgsz 1280 --batch 4
    python train.py --model rtdetr-l --batch 1

Le run est nommé d'après le modèle ; poids et métriques dans bloc_detection/runs/.
"""
from __future__ import annotations
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "dataset" / "data.yaml"),
                    help="data.yaml (dataset/ = 6 classes, dataset_blocs/ = 1 classe)")
    ap.add_argument("--model", default="yolo11s",
                    help="yolo11n/s/m... ou rtdetr-l. Ajoute .pt si poids pré-entraînés.")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--device", default=0, help="0 pour GPU, 'cpu' sinon")
    ap.add_argument("--name", default=None, help="nom du run (défaut = modèle)")
    args = ap.parse_args()

    DATA = Path(args.data)
    if not DATA.exists():
        raise SystemExit(f"{DATA} introuvable. Lance d'abord export_dataset.py.")

    from ultralytics import YOLO, RTDETR

    weights = args.model if args.model.endswith(".pt") else f"{args.model}.pt"
    Model = RTDETR if "rtdetr" in args.model.lower() else YOLO
    model = Model(weights)

    model.train(
        data=str(DATA),
        imgsz=args.imgsz,
        batch=args.batch,
        epochs=args.epochs,
        patience=args.patience,
        device=args.device,
        amp=True,                       # mixed precision -> économise la VRAM
        project=str(ROOT / "runs"),
        name=args.name or args.model,
        exist_ok=True,
    )


if __name__ == "__main__":
    main()
