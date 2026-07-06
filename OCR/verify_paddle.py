"""
Vérification PaddleOCR : télécharge un journal, lance le pipeline complet,
et sauvegarde deux fichiers :
  - {ark}_layout.json  : blocs avec positions (structure géométrique)
  - {ark}_text.txt     : texte OCR dans la même organisation (colonne/bloc)

Usage :
    conda run -n oldspapers python OCR/verify_paddle.py [ARK]
    Défaut : bpt6k412758h (La Croix, 1930-05-27)
"""
import json
import pathlib
import sys

# Permettre l'import depuis la racine du projet
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ARK = sys.argv[1] if len(sys.argv) > 1 else "bpt6k412758h"

CACHE_DIR    = ROOT / "cache" / "ocr"
IMG_CACHE_DIR = ROOT / "cache" / "ocr_img"
OUT_DIR      = ROOT / "cache" / "verify"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"=== Vérification PaddleOCR — {ARK} ===")

# ── Supprimer le cache paddle pour forcer un recalcul ─────────────────────────
cache_paddle = CACHE_DIR / f"{ARK}_layout_paddle.json"
if cache_paddle.exists():
    cache_paddle.unlink()
    print(f"Cache paddle supprimé : {cache_paddle.name}")

# ── Lancer le pipeline ────────────────────────────────────────────────────────
from OCR.ocr_local import run_layout_blocks

print("Lancement pipeline (téléchargement + PaddleOCR)…")
result = run_layout_blocks(ARK, CACHE_DIR, IMG_CACHE_DIR)

if "error" in result:
    print(f"\n[ERREUR] {result['error']}")
    sys.exit(1)

blocks = result["blocks"]
img_w  = result["img_w"]
img_h  = result["img_h"]
n_cols = result["n_cols"]
engine = result.get("engine", "?")

print(f"  engine={engine}  colonnes={n_cols}  blocs={len(blocks)}")

# ── Fichier 1 : layout (positions + label, sans texte) ───────────────────────
layout_blocks = []
for b in blocks:
    layout_blocks.append({
        "position":   b["position"],
        "label":      b["label"],
        "col":        round(b["x0"] * n_cols),   # numéro de colonne approximatif
        "x0": b["x0"], "y0": b["y0"],
        "x1": b["x1"], "y1": b["y1"],
        "w":  round(b["x1"] - b["x0"], 4),
        "h":  round(b["y1"] - b["y0"], 4),
        "confidence": b.get("confidence", 0),
    })

layout_out = {
    "ark":    ARK,
    "engine": engine,
    "img_w":  img_w,
    "img_h":  img_h,
    "n_cols": n_cols,
    "n_blocks": len(blocks),
    "blocks": layout_blocks,
}

layout_path = OUT_DIR / f"{ARK}_layout.json"
layout_path.write_text(
    json.dumps(layout_out, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
print(f"  → {layout_path.name}")

# ── Fichier 2 : texte OCR organisé par colonne/bloc ──────────────────────────
lines_text = []
lines_text.append(f"=== OCR — {ARK} | {engine} | {n_cols} colonnes | {len(blocks)} blocs ===")
lines_text.append(f"    Image : {img_w}×{img_h} px")
lines_text.append("")

# Regrouper par colonne (arrondi du x0)
from collections import defaultdict
by_col = defaultdict(list)
for b in blocks:
    col_num = round(b["x0"] * n_cols)
    by_col[col_num].append(b)

for col_num in sorted(by_col.keys()):
    col_blocks = sorted(by_col[col_num], key=lambda b: b["y0"])
    lines_text.append(f"{'─'*60}")
    lines_text.append(f"  COLONNE {col_num + 1}  ({len(col_blocks)} blocs)")
    lines_text.append(f"{'─'*60}")
    for b in col_blocks:
        pct_w = round((b["x1"] - b["x0"]) * 100)
        pct_h = round((b["y1"] - b["y0"]) * 100)
        lines_text.append(
            f"\n  [{b['position']:02d}] {b['label']:<6}  "
            f"y={b['y0']:.3f}-{b['y1']:.3f}  ({pct_w}%L × {pct_h}%H)  "
            f"conf={b.get('confidence', 0):.2f}"
        )
        # Texte du bloc — indenté
        text = b.get("text", "")
        for seg in (text[i:i+90] for i in range(0, len(text), 90)):
            lines_text.append(f"    {seg}")
    lines_text.append("")

text_path = OUT_DIR / f"{ARK}_text.txt"
text_path.write_text("\n".join(lines_text), encoding="utf-8")
print(f"  → {text_path.name}")

print(f"\nFichiers dans : {OUT_DIR}")
print("Terminé.")
