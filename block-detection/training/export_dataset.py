"""
Exporte les annotations de l'outil d'annotation (SQLite) vers un dataset au
format Ultralytics YOLO, utilisable tel quel par YOLO11 et RT-DETR.

Sortie (dans bloc_detection/dataset/) :
    images/train/<slug>.jpg   labels/train/<slug>.txt
    images/val/<slug>.jpg     labels/val/<slug>.txt
    data.yaml

Chaque .txt : une ligne par boîte -> "<class_id> <cx> <cy> <w> <h>" normalisé [0,1].
Les images sont liées en dur (os.link) pour ne pas dupliquer des Go de JPG ;
copie en repli si le lien dur échoue (volume différent, FS sans hardlink).

On n'exporte que les images au statut 'done'. Le split train/val est déterministe
(hash du slug) pour rester stable d'un export à l'autre.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT.parent / "annotation_tool" / "data" / "annotations.db"
OUT_DIR_MULTI = ROOT / "dataset"          # 6 classes
OUT_DIR_SINGLE = ROOT / "dataset_blocs"   # 1 classe 'bloc' (class-agnostic)


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise SystemExit(f"Base introuvable : {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _label_map(conn, exclude: set[str] = frozenset()) -> tuple[dict[int, int], list[str]]:
    """Retourne (label_id_db -> class_index 0..N-1, noms ordonnés).
    Les classes dans `exclude` (par nom) sont retirées : leurs boîtes seront
    ignorées à l'export et les index restants restent contigus (0..N-1)."""
    rows = conn.execute("SELECT id, name FROM labels ORDER BY id").fetchall()
    rows = [r for r in rows if r["name"] not in exclude]
    id_to_idx = {r["id"]: i for i, r in enumerate(rows)}
    names = [r["name"] for r in rows]
    return id_to_idx, names


def _split(slug: str, val_ratio: float, seed: int) -> str:
    """Split déterministe : 'val' ou 'train' selon un hash stable du slug."""
    h = hashlib.md5(f"{seed}:{slug}".encode()).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    return "val" if frac < val_ratio else "train"


def _link_or_copy(src: Path, dst: Path):
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)          # lien dur : pas de copie de données
    except OSError:
        shutil.copy2(src, dst)     # repli


def export(val_ratio: float = 0.2, seed: int = 42, clean: bool = True,
           single_class: bool = False, exclude: set[str] = frozenset()) -> dict:
    conn = _connect()
    id_to_idx, names = _label_map(conn, exclude=exclude)

    # Mode class-agnostic : on écrase le mapping pour tout envoyer sur 'bloc'.
    # Utile pour un premier modèle d'aide à l'annotation (détecte les boîtes
    # sans se soucier du type, beaucoup plus facile à apprendre sur peu d'images).
    if single_class:
        id_to_idx = {lid: 0 for lid in id_to_idx}
        names = ["bloc"]

    OUT_DIR = OUT_DIR_SINGLE if single_class else OUT_DIR_MULTI
    if clean and OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    for split in ("train", "val"):
        (OUT_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)

    images = conn.execute(
        "SELECT id, slug, journal, path, w, h FROM images WHERE status='done'"
    ).fetchall()

    counts = {"train": 0, "val": 0}
    boxes_total = 0
    skipped_no_box = 0
    per_class = {n: 0 for n in names}

    for img in images:
        annos = conn.execute(
            "SELECT label_id, x0, y0, x1, y1 FROM annotations WHERE image_id=?",
            (img["id"],),
        ).fetchall()
        if not annos:
            skipped_no_box += 1
            continue

        src = Path(img["path"])
        if not src.is_absolute():
            src = (ROOT.parent / "annotation_tool" / src).resolve()
        if not src.exists():
            print(f"  ! image manquante, ignorée : {img['slug']} ({src})")
            continue

        W, H = img["w"], img["h"]
        if not W or not H:
            print(f"  ! dimensions manquantes, ignorée : {img['slug']}")
            continue

        split = _split(img["slug"], val_ratio, seed)
        lines = []
        for a in annos:
            cls = id_to_idx.get(a["label_id"])
            if cls is None:
                continue
            x0, x1 = sorted((a["x0"], a["x1"]))
            y0, y1 = sorted((a["y0"], a["y1"]))
            # clip dans l'image
            x0, x1 = max(0, x0), min(W, x1)
            y0, y1 = max(0, y0), min(H, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            cx = (x0 + x1) / 2 / W
            cy = (y0 + y1) / 2 / H
            bw = (x1 - x0) / W
            bh = (y1 - y0) / H
            lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            per_class[names[cls]] += 1

        if not lines:
            skipped_no_box += 1
            continue

        dst_img = OUT_DIR / "images" / split / f"{img['slug']}{src.suffix}"
        dst_lbl = OUT_DIR / "labels" / split / f"{img['slug']}.txt"
        _link_or_copy(src, dst_img)
        dst_lbl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        counts[split] += 1
        boxes_total += len(lines)

    conn.close()

    # data.yaml
    yaml_lines = [
        f"path: {OUT_DIR.as_posix()}",
        "train: images/train",
        "val: images/val",
        "",
        "names:",
    ]
    yaml_lines += [f"  {i}: {n}" for i, n in enumerate(names)]
    (OUT_DIR / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")

    return {
        "names": names,
        "train": counts["train"],
        "val": counts["val"],
        "boxes": boxes_total,
        "skipped_no_box": skipped_no_box,
        "per_class": per_class,
        "data_yaml": OUT_DIR / "data.yaml",
    }


def main():
    ap = argparse.ArgumentParser(description="DB d'annotation -> dataset YOLO")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-clean", action="store_true",
                    help="ne pas vider le dossier de sortie avant export")
    ap.add_argument("--single-class", action="store_true",
                    help="fusionne tout en une classe 'bloc' -> dataset_blocs/")
    ap.add_argument("--exclude", default="",
                    help="noms de classes à exclure, séparés par des virgules (ex: 'autres')")
    args = ap.parse_args()

    exclude = {s.strip() for s in args.exclude.split(",") if s.strip()}
    r = export(val_ratio=args.val_ratio, seed=args.seed, clean=not args.no_clean,
               single_class=args.single_class, exclude=exclude)

    print("\n=== Export terminé ===")
    print(f"Classes : {r['names']}")
    print(f"Images   train={r['train']}  val={r['val']}")
    print(f"Boîtes   total={r['boxes']}   (images sans boîte ignorées : {r['skipped_no_box']})")
    print("Par classe :")
    for n, c in r["per_class"].items():
        print(f"  {n:<16} {c}")
    print(f"\ndata.yaml -> {r['data_yaml']}")
    if r["val"] == 0:
        print("\n⚠  val vide : trop peu d'images. Baisse --val-ratio ou annote plus.")
    if r["train"] == 0:
        print("⚠  train vide : aucune image 'done' avec des boîtes.")


if __name__ == "__main__":
    main()
