"""Tests unitaires pour db.py (couche SQLite)."""
from __future__ import annotations
import db

DEFAULT_LABEL_NAMES = {
    "header", "titre", "illustration", "bloc de texte", "texte isolé", "autres"
}


def _img(slug="a", journal="Le Figaro", date="1930-05-25", path="x.jpg"):
    return db.add_image(slug, journal, date, "cb1", None, path, 100, 200)


# ─── Initialisation et labels ────────────────────────────────────────────

def test_init_creates_default_labels():
    names = {l["name"] for l in db.list_labels()}
    assert names == DEFAULT_LABEL_NAMES
    for l in db.list_labels():
        assert l["color"].startswith("#") and len(l["color"]) == 7


def test_init_is_idempotent():
    """Reappeler init_db ne duplique ni le schema ni les labels."""
    before = len(db.list_labels())
    db.init_db()
    db.init_db()
    assert len(db.list_labels()) == before


def test_reset_labels_wipes_annotations():
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(img_id, label_id, 0, 0, 10, 10)
    assert db.stats()["annotations"] == 1
    db.init_db(reset_labels=True)
    assert db.stats()["annotations"] == 0
    # Les labels par defaut sont reseedees
    assert len(db.list_labels()) == len(DEFAULT_LABEL_NAMES)


# ─── Migration de noms de labels ─────────────────────────────────────────

def test_label_rename_preserves_id_and_annotations(monkeypatch, tmp_path):
    """Renommer 'texte' -> 'bloc de texte' doit conserver les annotations existantes."""
    # On simule une DB plus ancienne : on la seed avec l'ancien jeu de labels.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "old.db")
    monkeypatch.setattr(db, "DEFAULT_LABELS", [
        ("header",       "#f15bb5", "Bandeau"),
        ("titre",        "#e76f51", "Titre"),
        ("illustration", "#2a9d8f", "Image"),
        ("texte",        "#e9c46a", "Corps de texte"),
        ("autres",       "#888888", "Divers"),
    ])
    monkeypatch.setattr(db, "LABEL_RENAMES", [])  # pas encore de migration
    db.init_db()

    # Cree une image + une annotation sur l'ancien label 'texte'.
    img_id = _img(slug="legacy")
    texte_id = next(l["id"] for l in db.list_labels() if l["name"] == "texte")
    anno_id = db.add_annotation(img_id, texte_id, 0, 0, 10, 10)

    # On bascule sur le nouveau jeu (avec migration + nouveau label) et on re-init.
    monkeypatch.setattr(db, "DEFAULT_LABELS", [
        ("header",         "#f15bb5", "Bandeau"),
        ("titre",          "#e76f51", "Titre"),
        ("illustration",   "#2a9d8f", "Image"),
        ("bloc de texte",  "#e9c46a", "Corps d'article"),
        ("texte isolé",    "#f4a261", "Texte isole"),
        ("autres",         "#888888", "Divers"),
    ])
    monkeypatch.setattr(db, "LABEL_RENAMES", [("texte", "bloc de texte")])
    db.init_db()

    # L'ancien label 'texte' a disparu, 'bloc de texte' existe et conserve le MEME id.
    names = {l["name"] for l in db.list_labels()}
    assert "texte" not in names
    assert "bloc de texte" in names
    assert "texte isolé" in names

    new_id = next(l["id"] for l in db.list_labels() if l["name"] == "bloc de texte")
    assert new_id == texte_id  # id preserve -> les annotations restent rattachees

    [a] = db.list_annotations(img_id)
    assert a["id"] == anno_id
    assert a["label_name"] == "bloc de texte"


def test_label_rename_skipped_if_target_already_exists(monkeypatch, tmp_path):
    """Si l'ancien ET le nouveau label coexistent, la migration ne touche a rien."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mixed.db")
    monkeypatch.setattr(db, "DEFAULT_LABELS", [
        ("texte",          "#aaa", "vieux"),
        ("bloc de texte",  "#bbb", "neuf"),
    ])
    monkeypatch.setattr(db, "LABEL_RENAMES", [("texte", "bloc de texte")])
    db.init_db()
    names = [l["name"] for l in db.list_labels()]
    assert "texte" in names and "bloc de texte" in names


# ─── Images ──────────────────────────────────────────────────────────────

def test_add_image_dedups_by_slug():
    a = _img(slug="dup", path="first.jpg")
    b = _img(slug="dup", path="second.jpg")
    assert a == b
    assert len(db.list_images()) == 1


def test_existing_slugs():
    assert db.existing_slugs() == set()
    _img(slug="a")
    _img(slug="b")
    assert db.existing_slugs() == {"a", "b"}


def test_new_image_default_status_is_todo():
    img_id = _img()
    assert db.get_image(img_id)["status"] == "todo"


def test_set_image_status_transitions():
    img_id = _img()
    for s in ("in_progress", "done", "skipped", "todo"):
        db.set_image_status(img_id, s)
        assert db.get_image(img_id)["status"] == s


def test_count_todo_only_counts_todo():
    a = _img(slug="a")
    b = _img(slug="b")
    _img(slug="c")
    assert db.count_todo() == 3
    db.set_image_status(a, "in_progress")
    db.set_image_status(b, "done")
    assert db.count_todo() == 1


def test_get_image_returns_none_for_missing_id():
    assert db.get_image(999) is None


# ─── Regle metier : ne pas re-proposer une image done ────────────────────

def test_paginated_excludes_done_and_skipped_by_default():
    """Une image marquee 'done' ne doit JAMAIS revenir dans la file de travail."""
    todo_id     = _img(slug="todo")
    progress_id = _img(slug="prog")
    done_id     = _img(slug="done")
    skipped_id  = _img(slug="skip")

    db.set_image_status(progress_id, "in_progress")
    db.set_image_status(done_id, "done")
    db.set_image_status(skipped_id, "skipped")

    ids = {i["id"] for i in db.list_images_paginated(limit=10)}
    assert ids == {todo_id, progress_id}


def test_paginated_respects_limit():
    for i in range(5):
        _img(slug=f"s{i}")
    assert len(db.list_images_paginated(limit=3)) == 3


def test_paginated_includes_annotation_count():
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(img_id, label_id, 0, 0, 10, 10)
    db.add_annotation(img_id, label_id, 20, 20, 30, 30)
    [out] = db.list_images_paginated(limit=10)
    assert out["n_annotations"] == 2


def test_paginated_custom_status_filter():
    a = _img(slug="a")
    db.set_image_status(a, "done")
    ids = {i["id"] for i in db.list_images_paginated(limit=10, statuses=("done",))}
    assert ids == {a}


# ─── Annotations CRUD ────────────────────────────────────────────────────

def test_add_and_list_annotation():
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    anno_id = db.add_annotation(img_id, label_id, 10, 20, 30, 40)
    [a] = db.list_annotations(img_id)
    assert a["id"] == anno_id
    assert (a["x0"], a["y0"], a["x1"], a["y1"]) == (10, 20, 30, 40)
    assert a["label_name"] == db.list_labels()[0]["name"]
    assert a["label_color"] == db.list_labels()[0]["color"]


def test_list_annotations_empty_for_unannotated_image():
    img_id = _img()
    assert db.list_annotations(img_id) == []


def test_update_annotation_geometry_preserves_label():
    img_id = _img()
    l1 = db.list_labels()[0]
    anno_id = db.add_annotation(img_id, l1["id"], 0, 0, 10, 10)
    db.update_annotation(anno_id, 5, 5, 50, 50)
    [a] = db.list_annotations(img_id)
    assert (a["x0"], a["y0"], a["x1"], a["y1"]) == (5, 5, 50, 50)
    assert a["label_id"] == l1["id"]


def test_update_annotation_can_change_label():
    img_id = _img()
    l1, l2 = db.list_labels()[:2]
    anno_id = db.add_annotation(img_id, l1["id"], 0, 0, 10, 10)
    db.update_annotation(anno_id, 0, 0, 10, 10, label_id=l2["id"])
    [a] = db.list_annotations(img_id)
    assert a["label_id"] == l2["id"]


def test_delete_annotation():
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    anno_id = db.add_annotation(img_id, label_id, 0, 0, 10, 10)
    db.delete_annotation(anno_id)
    assert db.list_annotations(img_id) == []


def test_deleting_image_cascades_to_annotations():
    """La FK ON DELETE CASCADE doit nettoyer les bbox orphelines."""
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(img_id, label_id, 0, 0, 10, 10)
    db.add_annotation(img_id, label_id, 20, 20, 30, 30)
    conn = db.get_conn()
    conn.execute("DELETE FROM images WHERE id = ?", (img_id,))
    conn.commit()
    conn.close()
    assert db.list_annotations(img_id) == []
    assert db.stats()["annotations"] == 0


# ─── Persistance : la garantie au coeur de la question utilisateur ──────

def test_annotations_persist_across_reconnections():
    """Annoter, fermer la connexion, rouvrir : tout doit etre intact.

    `get_conn` ouvre une nouvelle connexion SQLite a chaque appel, donc
    enchainer les helpers simule un cycle complet ferme/reouvre l'outil.
    """
    img_id = _img(slug="persist-me")
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(img_id, label_id, 1, 2, 3, 4)
    db.add_annotation(img_id, label_id, 5, 6, 7, 8)
    db.set_image_status(img_id, "done")

    annos = db.list_annotations(img_id)
    assert len(annos) == 2
    assert {(a["x0"], a["y0"], a["x1"], a["y1"]) for a in annos} == {(1, 2, 3, 4), (5, 6, 7, 8)}
    assert db.get_image(img_id)["status"] == "done"


# ─── Stats ───────────────────────────────────────────────────────────────

def test_stats_breakdown():
    a = _img(slug="a")
    _img(slug="b")
    db.set_image_status(a, "done")
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(a, label_id, 0, 0, 1, 1)

    s = db.stats()
    assert s["total"] == 2
    assert s["done"] == 1
    assert s["todo"] == 1
    assert s["in_progress"] == 0
    assert s["annotations"] == 1
    assert set(s["by_label"]) == DEFAULT_LABEL_NAMES
