"""Fixtures pour les tests de training/. Rend importables les scripts de training
(export_dataset, suggest, review_proposals) ET le `db` de l'outil d'annotation
(dont dépend l'export). Tout est hermétique : DB SQLite temporaire, images
minuscules générées, aucun modèle chargé."""
from __future__ import annotations
import sys, pathlib, types
import pytest

_TRAINING = pathlib.Path(__file__).resolve().parents[1]        # block-detection/training
_ANNOT = _TRAINING.parent / "annotation"                       # block-detection/annotation
for _p in (_TRAINING, _ANNOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    """DB annotation temporaire + dossiers de sortie isolés, pour tester export().
    Renvoie un namespace avec `.ed` (module export_dataset), `.db`, `.tmp`,
    et un helper `.add(slug, boxes)` qui crée une image 'done' + ses annotations."""
    from PIL import Image
    import db as anndb
    import export_dataset as ed

    dbfile = tmp_path / "annotations.db"
    monkeypatch.setattr(anndb, "DB_PATH", dbfile)
    monkeypatch.setattr(ed, "DB_PATH", dbfile)
    monkeypatch.setattr(ed, "OUT_DIR_MULTI", tmp_path / "dataset")
    monkeypatch.setattr(ed, "OUT_DIR_SINGLE", tmp_path / "dataset_blocs")
    anndb.init_db()

    imgs = tmp_path / "imgs"; imgs.mkdir()

    def add(slug, boxes, w=1000, h=800, journal="Le Figaro", status="done"):
        """boxes = [(label_name, x0, y0, x1, y1), ...]. Renvoie l'image_id."""
        p = imgs / f"{slug}.jpg"
        Image.new("RGB", (w, h), (200, 200, 200)).save(p, "JPEG")
        iid = anndb.add_image(slug, journal, "1930-01-01", "cb1", None, str(p), w, h)
        anndb.set_image_status(iid, status)
        name_to_id = {l["name"]: l["id"] for l in anndb.list_labels()}
        for (name, x0, y0, x1, y1) in boxes:
            anndb.add_annotation(iid, name_to_id[name], x0, y0, x1, y1)
        return iid

    return types.SimpleNamespace(ed=ed, db=anndb, tmp=tmp_path, add=add)
