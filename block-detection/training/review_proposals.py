"""
Génère des propositions de correction d'annotation (mode révision de l'outil),
selon la convention (cf. CONVENTION_ANNOTATION.md), en comparant les prédictions
du modèle multi-classes aux annotations humaines existantes.

Deux types de propositions :
  - split_title : le modèle voit un `titre` (gras, sur sa ligne) DANS une de tes
                  boîtes `bloc de texte`/`texte isolé` -> proposer de séparer le
                  titre et de recouper le bloc autour.
  - reclassify  : une de tes boîtes correspond (position) à une prédiction d'une
                  AUTRE classe -> proposer de changer la classe.

Chaque proposition = {before:[annos à retirer], after:[annos à créer], region, conf}.
Rien n'est modifié : on écrit dans la table `proposals`, l'UI accepte/refuse/corrige.

À lancer dans l'env bloc_detection :
    python review_proposals.py --image-id 42
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ANNOT_DIR = ROOT.parent / "annotation"
DEFAULT_WEIGHTS = ROOT / "runs" / "multiclass_yolo11s_v3" / "weights" / "best.pt"

sys.path.insert(0, str(ANNOT_DIR))
import db  # noqa: E402

CONF_TITLE = 0.40      # confiance min. pour proposer de séparer/ajouter un titre
CONF_RECLASS = 0.50    # confiance min. pour proposer une reclassification
INSIDE_RATIO = 0.60    # part du titre qui doit être dans le bloc hôte
MIN_H = 12             # hauteur min. (px) d'un morceau après découpe
HOST_CLASSES = {"bloc de texte", "texte isolé"}


def iou(a, b):
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1]); x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    iw = max(0, x1 - x0); ih = max(0, y1 - y0); inter = iw * ih
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def inside_ratio(inner, outer):
    """Part de `inner` couverte par `outer` (intersection / aire de inner)."""
    x0 = max(inner[0], outer[0]); y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2]); y1 = min(inner[3], outer[3])
    iw = max(0, x1 - x0); ih = max(0, y1 - y0)
    ai = (inner[2]-inner[0]) * (inner[3]-inner[1])
    return (iw * ih) / ai if ai > 0 else 0.0


def build_proposals(gt, preds, id_by_name, W, H):
    """gt: annotations existantes (dict). preds: [(name,x0,y0,x1,y1,conf)]."""
    proposals = []
    used_gt = set()                     # ids GT déjà touchés (évite les conflits)

    gt_titres = [g for g in gt if g["label_name"] == "titre"]
    titre_preds = [p for p in preds
                   if p[0] == "titre" and p[5] >= CONF_TITLE
                   and max((iou(p[1:5], t_box(g)) for g in gt_titres), default=0) < 0.5]

    hosts = [g for g in gt if g["label_name"] in HOST_CLASSES]

    # 1) split_title : regrouper les titres détectés par bloc hôte
    for h in hosts:
        hb = t_box(h)
        inside = sorted(
            [p for p in titre_preds if inside_ratio(p[1:5], hb) >= INSIDE_RATIO],
            key=lambda p: p[2],
        )
        if not inside:
            continue
        # découpe verticale du bloc hôte aux bandes des titres
        after = []
        cur_y = hb[1]
        for p in inside:
            ty0, ty1 = p[2], p[4]
            if ty0 - cur_y >= MIN_H:      # morceau de texte au-dessus du titre
                after.append(mk_box(h["label_id"], h["label_name"], hb[0], cur_y, hb[2], ty0))
            after.append(mk_box(id_by_name["titre"], "titre",
                                max(hb[0], p[1]), ty0, min(hb[2], p[3]), ty1, p[5]))
            cur_y = ty1
        if hb[3] - cur_y >= MIN_H:        # morceau de texte sous le dernier titre
            after.append(mk_box(h["label_id"], h["label_name"], hb[0], cur_y, hb[2], hb[3]))
        n_tit = sum(1 for a in after if a["label_name"] == "titre")
        proposals.append({
            "ptype": "split_title",
            "descr": (f"Séparer {n_tit} titre(s) dans « {h['label_name']} »"),
            "payload": {"before": [before_of(h)], "after": after,
                        "region": pad_region([hb], W, H),
                        "conf": round(max(p[5] for p in inside), 2)},
        })
        used_gt.add(h["id"])
        for p in inside:
            titre_preds.remove(p)

    # 2) titres détectés hors de tout bloc hôte -> proposer de les ajouter
    for p in titre_preds:
        if any(inside_ratio(p[1:5], t_box(h)) >= INSIDE_RATIO for h in hosts):
            continue
        b = mk_box(id_by_name["titre"], "titre", p[1], p[2], p[3], p[4], p[5])
        proposals.append({
            "ptype": "split_title",
            "descr": "Ajouter un titre non annoté",
            "payload": {"before": [], "after": [b],
                        "region": pad_region([p[1:5]], W, H), "conf": round(p[5], 2)},
        })

    # 3) reclassify : boîte GT bien localisée mais autre classe côté modèle
    for g in gt:
        if g["id"] in used_gt:
            continue
        gb = t_box(g)
        best = max(preds, key=lambda p: iou(gb, p[1:5]), default=None)
        if not best or iou(gb, best[1:5]) < 0.5:
            continue
        if best[0] == g["label_name"] or best[5] < CONF_RECLASS:
            continue
        if best[0] not in id_by_name:
            continue
        proposals.append({
            "ptype": "reclassify",
            "descr": f"Reclasser « {g['label_name']} » → « {best[0]} »",
            "payload": {
                "before": [before_of(g)],
                "after": [mk_box(id_by_name[best[0]], best[0], gb[0], gb[1], gb[2], gb[3], best[5])],
                "region": pad_region([gb], W, H), "conf": round(best[5], 2)},
        })
        used_gt.add(g["id"])

    return proposals


def t_box(g):
    return (g["x0"], g["y0"], g["x1"], g["y1"])


def before_of(g):
    return {"id": g["id"], "label_id": g["label_id"], "label_name": g["label_name"],
            "x0": g["x0"], "y0": g["y0"], "x1": g["x1"], "y1": g["y1"]}


def mk_box(label_id, label_name, x0, y0, x1, y1, conf=None):
    return {"label_id": int(label_id), "label_name": label_name,
            "x0": int(round(x0)), "y0": int(round(y0)),
            "x1": int(round(x1)), "y1": int(round(y1)),
            **({"conf": round(float(conf), 2)} if conf is not None else {})}


def pad_region(boxes, W, H, pad=70):
    x0 = min(b[0] for b in boxes) - pad; y0 = min(b[1] for b in boxes) - pad
    x1 = max(b[2] for b in boxes) + pad; y1 = max(b[3] for b in boxes) + pad
    return [max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))]


def process_image(model, names, img, id_by_name, conf, imgsz, device):
    src = Path(img["path"])
    if not src.is_absolute():
        src = (ANNOT_DIR / src).resolve()
    if not src.exists():
        print(f"  ! image manquante : {img['slug']}")
        return 0
    gt = db.list_annotations(img["id"])
    res = model.predict(source=str(src), conf=conf, imgsz=imgsz,
                        device=device, verbose=False)[0]
    preds = [(names[int(b.cls)], *[float(v) for v in b.xyxy[0]], float(b.conf))
             for b in res.boxes]
    proposals = build_proposals(gt, preds, id_by_name, img["w"], img["h"])
    db.replace_proposals(img["id"], proposals)
    return len(proposals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    ap.add_argument("--image-id", type=int, default=0, help="une seule image")
    ap.add_argument("--all", action="store_true", help="toutes les unes des statuts --status")
    ap.add_argument("--status", default="done", help="statuts à traiter (séparés par virgule)")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--device", default=0)
    args = ap.parse_args()

    if not Path(args.weights).exists():
        raise SystemExit(f"Poids introuvables : {args.weights}")

    db.init_db()
    if args.image_id:
        images = [db.get_image(args.image_id)]
        if not images[0]:
            raise SystemExit(f"Image {args.image_id} introuvable.")
    elif args.all:
        images = []
        for st in [s.strip() for s in args.status.split(",") if s.strip()]:
            images += db.list_images(status=st)
    else:
        raise SystemExit("Préciser --image-id N ou --all.")

    id_by_name = {l["name"]: l["id"] for l in db.list_labels()}
    from ultralytics import YOLO
    model = YOLO(args.weights)        # chargé une seule fois
    names = model.names

    total = 0
    for img in images:
        n = process_image(model, names, img, id_by_name,
                          args.conf, args.imgsz, args.device)
        total += n
        print(f"  {img['slug']:<30} {n} propositions", flush=True)
    print(f"\n{len(images)} unes traitées, {total} propositions au total.", flush=True)


if __name__ == "__main__":
    main()
