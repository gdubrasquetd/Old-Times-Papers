"""Publie un summary.json vers le cache de l'app, indexé par ARK de fascicule.

La pipeline travaille par slug (`le_temps_1936-08-08`), l'app par ARK Gallica
(`bpt6k262931k`). Le pont existe déjà : la table `images` de la base d'annotation
porte la colonne `issue_ark`, renseignée pour toutes les unes téléchargées.

    python publish_summary.py <summary.json | slug> [...]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "block-detection" / "annotation" / "data" / "annotations.db"
OUT_DIR = Path(__file__).resolve().parent / "out"
APP_CACHE = ROOT / "app" / "cache" / "summary"


def slug_to_ark(slug, db_path=DB_PATH):
    """ARK du fascicule pour un slug, ou None (base absente, slug inconnu, ARK vide)."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("select issue_ark from images where slug = ?", (slug,)).fetchone()
    finally:
        con.close()
    return row[0] if row and row[0] else None


def publish(summary_path, cache_dir=APP_CACHE, db_path=DB_PATH):
    """Copie summary.json vers <cache_dir>/<ark>.json. Renvoie le chemin écrit, ou None
    si l'ARK est introuvable (on n'écrit rien plutôt que d'écrire sous un mauvais nom)."""
    data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    ark = data.get("issue_ark") or slug_to_ark(data["slug"], db_path)
    if not ark:
        return None
    data["issue_ark"] = ark
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{ark}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _resolve_arg(arg):
    """Accepte un chemin de summary.json ou un simple slug."""
    p = Path(arg)
    return p if p.suffix == ".json" else OUT_DIR / arg / "summary.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="summary.json ou slug")
    args = ap.parse_args()
    failed = 0
    for t in args.targets:
        src = _resolve_arg(t)
        if not src.exists():
            print(f"  absent : {src}", flush=True); failed += 1; continue
        out = publish(src)
        if out is None:
            print(f"  ARK introuvable pour {src.parent.name} — non publié", flush=True)
            failed += 1
        else:
            print(f"  {src.parent.name} -> {out}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
