"""
Pré-calcule des suggestions de boîtes (modèle class-agnostic) et les écrit dans
la base de l'outil d'annotation. L'UI d'annotation peut ensuite les matérialiser
en annotations éditables (bouton « Charger les suggestions »).

À lancer dans l'env `bloc_detection` (a besoin de torch/ultralytics) :

    python bloc_detection/suggest.py
    python bloc_detection/suggest.py --conf 0.4 --status todo,in_progress

Écrit dans annotation_tool/data/annotations.db (table `suggestions`). N'altère
jamais les annotations humaines.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ANNOT_DIR = ROOT.parent / "annotation"
DEFAULT_WEIGHTS = ROOT / "runs" / "blocs_yolo11s" / "weights" / "best.pt"

# db.py de l'outil d'annotation (stdlib pure : importable dans cet env aussi)
sys.path.insert(0, str(ANNOT_DIR))
import db  # noqa: E402


def _overlap_min(a, b) -> float:
    """Recouvrement de deux boîtes relatif à la PLUS PETITE : intersection /
    min(aireA, aireB). Vaut ~1 si l'une est presque entièrement dans l'autre
    (cas containment), contrairement à l'IoU qui resterait faible. Boîtes au
    format (x0, y0, x1, y1, ...)."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix1 - ix0, iy1 - iy0
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    smaller = min(area_a, area_b)
    return inter / smaller if smaller > 0 else 0.0


def suppress_overlaps(boxes, thresh: float):
    """Supprime les boîtes qui se chevauchent grandement (doublons + containment).
    Greedy par confiance décroissante : on garde la plus confiante, puis on jette
    toute boîte dont le recouvrement (relatif à la plus petite) avec une boîte déjà
    gardée dépasse `thresh`. Renvoie (boîtes_gardées, nb_supprimées)."""
    order = sorted(boxes, key=lambda b: b[4], reverse=True)
    kept = []
    for b in order:
        if any(_overlap_min(b, k) > thresh for k in kept):
            continue
        kept.append(b)
    return kept, len(boxes) - len(kept)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    ap.add_argument("--conf", type=float, default=0.4)
    ap.add_argument("--dedup", type=float, default=0.85,
                    help="supprime les boîtes recouvertes à plus de ce ratio "
                         "(intersection/aire de la plus petite). 0 = désactivé.")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=0)
    ap.add_argument("--status", default="todo",
                    help="statuts à traiter, séparés par des virgules (todo,in_progress)")
    ap.add_argument("--image-id", type=int, default=0,
                    help="ne traiter qu'une image (à la demande depuis l'UI)")
    ap.add_argument("--limit", type=int, default=0, help="0 = pas de limite")
    args = ap.parse_args()

    if not Path(args.weights).exists():
        raise SystemExit(f"Poids introuvables : {args.weights}\n"
                         "Entraîne d'abord (train.py --data dataset_blocs/data.yaml).")

    db.init_db()  # garantit l'existence de la table suggestions
    if args.image_id:
        img = db.get_image(args.image_id)
        if not img:
            raise SystemExit(f"Image {args.image_id} introuvable.")
        images = [img]
    else:
        statuses = [s.strip() for s in args.status.split(",") if s.strip()]
        images = []
        for st in statuses:
            images += db.list_images(status=st)
        if args.limit:
            images = images[:args.limit]
    if not images:
        print(f"Aucune image à traiter.")
        return

    from ultralytics import YOLO
    model = YOLO(args.weights)
    model_name = Path(args.weights).parent.parent.name  # ex: blocs_yolo11s

    total_boxes = 0
    total_removed = 0
    for img in images:
        src = Path(img["path"])
        if not src.is_absolute():
            src = (ANNOT_DIR / src).resolve()
        if not src.exists():
            print(f"  ! image manquante : {img['slug']}")
            continue
        res = model.predict(source=str(src), conf=args.conf, imgsz=args.imgsz,
                            device=args.device, verbose=False)[0]
        boxes = []
        for b in res.boxes:
            x0, y0, x1, y1 = (float(v) for v in b.xyxy[0])
            boxes.append((x0, y0, x1, y1, float(b.conf)))
        removed = 0
        if args.dedup > 0 and len(boxes) > 1:
            boxes, removed = suppress_overlaps(boxes, args.dedup)
        db.replace_suggestions(img["id"], boxes, model=model_name)
        total_boxes += len(boxes)
        total_removed += removed
        suffix = f"  (−{removed} chevauchantes)" if removed else ""
        print(f"  {img['slug']:<28} {len(boxes):>3} boîtes{suffix}")

    dedup_msg = f", {total_removed} doublons supprimés" if args.dedup > 0 else ""
    print(f"\n{len(images)} unes traitées, {total_boxes} suggestions écrites "
          f"(conf≥{args.conf}, modèle={model_name}{dedup_msg}).")


if __name__ == "__main__":
    main()
