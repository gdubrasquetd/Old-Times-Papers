"""Tests des routes Flask via app.test_client() (fixture `client` dans conftest)."""
from __future__ import annotations
import pathlib
from PIL import Image

import db


def _real_img(tmp_path, slug="le_figaro_1930-05-25", w=1000, h=1400):
    """Cree une vraie image JPEG sur disque + son entree DB. Retourne l'id."""
    path = tmp_path / f"{slug}.jpg"
    Image.new("RGB", (w, h), color=(200, 200, 200)).save(path, "JPEG")
    return db.add_image(slug, "Le Figaro", "1930-05-25", "cb1", None,
                          str(path), w, h)


def _img(slug="le_figaro_1930-05-25", journal="Le Figaro"):
    return db.add_image(slug, journal, "1930-05-25", "cb1", None,
                          f"/tmp/{slug}.jpg", 100, 200)


# ─── Pages ───────────────────────────────────────────────────────────────

def test_index_returns_200(client):
    assert client.get("/").status_code == 200


def test_annotate_page_renders(client):
    img_id = _img()
    rv = client.get(f"/annotate/{img_id}")
    assert rv.status_code == 200


def test_annotate_unknown_image_returns_404(client):
    assert client.get("/annotate/9999").status_code == 404


def test_opening_todo_image_flips_to_in_progress(client):
    img_id = _img()
    assert db.get_image(img_id)["status"] == "todo"
    client.get(f"/annotate/{img_id}")
    assert db.get_image(img_id)["status"] == "in_progress"


def test_opening_done_image_keeps_done(client):
    """Reouvrir une image terminee ne doit PAS la retrograder en in_progress."""
    img_id = _img()
    db.set_image_status(img_id, "done")
    client.get(f"/annotate/{img_id}")
    assert db.get_image(img_id)["status"] == "done"


# ─── API annotations ─────────────────────────────────────────────────────

def test_post_annotation_persists(client):
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    rv = client.post("/api/annotations", json={
        "image_id": img_id, "label_id": label_id,
        "x0": 1, "y0": 2, "x1": 3, "y1": 4,
    })
    assert rv.status_code == 200
    anno_id = rv.get_json()["id"]
    [a] = db.list_annotations(img_id)
    assert a["id"] == anno_id
    assert (a["x0"], a["y0"], a["x1"], a["y1"]) == (1, 2, 3, 4)


def test_put_annotation_updates_geometry_and_label(client):
    img_id = _img()
    l1, l2 = db.list_labels()[:2]
    anno_id = db.add_annotation(img_id, l1["id"], 0, 0, 10, 10)
    rv = client.put(f"/api/annotations/{anno_id}", json={
        "x0": 5, "y0": 5, "x1": 50, "y1": 50, "label_id": l2["id"],
    })
    assert rv.status_code == 200
    [a] = db.list_annotations(img_id)
    assert (a["x0"], a["y0"], a["x1"], a["y1"]) == (5, 5, 50, 50)
    assert a["label_id"] == l2["id"]


def test_delete_annotation(client):
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    anno_id = db.add_annotation(img_id, label_id, 0, 0, 1, 1)
    rv = client.delete(f"/api/annotations/{anno_id}")
    assert rv.status_code == 200
    assert db.list_annotations(img_id) == []


def test_get_annotations_endpoint(client):
    img_id = _img()
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(img_id, label_id, 0, 0, 1, 1)
    rv = client.get(f"/api/image/{img_id}/annotations")
    assert rv.status_code == 200
    assert len(rv.get_json()) == 1


# ─── API statut ──────────────────────────────────────────────────────────

def test_status_update_endpoint(client):
    img_id = _img()
    rv = client.post(f"/api/image/{img_id}/status", json={"status": "done"})
    assert rv.status_code == 200
    assert db.get_image(img_id)["status"] == "done"


def test_in_progress_view_lists_only_in_progress_images(client):
    """L'onglet 'En cours' doit afficher exactement les images in_progress."""
    todo_id = _img(slug="t1")
    in_prog_id = _img(slug="p1")
    done_id = _img(slug="d1")
    db.set_image_status(in_prog_id, "in_progress")
    db.set_image_status(done_id, "done")

    rv = client.get("/?view=in_progress")
    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    # On verifie via les slugs presents dans le HTML.
    assert "p1" in body
    assert "t1" not in body
    assert "d1" not in body


def test_done_image_not_in_work_pool(client):
    """Regression : une image done ne doit pas reapparaitre dans la liste de travail."""
    img_id = _img()
    db.set_image_status(img_id, "done")
    # On declenche aussi la home pour s'assurer qu'aucune logique cote /
    # ne re-rebascule en todo.
    client.get("/")
    work_ids = {i["id"] for i in db.list_images_paginated(limit=20)}
    assert img_id not in work_ids


# ─── Divers endpoints ────────────────────────────────────────────────────

def test_get_labels_endpoint(client):
    rv = client.get("/api/labels")
    assert rv.status_code == 200
    names = {l["name"] for l in rv.get_json()}
    assert {"header", "titre", "illustration", "bloc de texte", "texte isolé", "autres"} <= names


def test_stats_endpoint(client):
    _img()
    rv = client.get("/api/stats")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["total"] == 1
    assert data["todo"] == 1


def test_export_skips_images_without_annotations(client):
    no_anno   = _img(slug="without_a")
    with_anno = _img(slug="with_a")
    label_id  = db.list_labels()[0]["id"]
    db.add_annotation(with_anno, label_id, 0, 0, 10, 10)

    rv = client.get("/api/export")
    assert rv.status_code == 200
    data = rv.get_json()
    slugs = {i["slug"] for i in data["images"]}
    assert "with_a" in slugs
    assert "without_a" not in slugs
    assert "labels" in data and "info" in data


def test_export_payload_shape(client):
    img_id = _img(slug="shape_test")
    label_id = db.list_labels()[0]["id"]
    db.add_annotation(img_id, label_id, 10, 20, 30, 40)

    data = client.get("/api/export").get_json()
    [img] = data["images"]
    assert img["slug"] == "shape_test"
    assert img["journal"] == "Le Figaro"
    assert img["date"] == "1930-05-25"
    [a] = img["annotations"]
    assert a["bbox"] == [10, 20, 30, 40]
    assert a["label"] == db.list_labels()[0]["name"]


def test_serve_image_returns_404_when_file_missing(client):
    """Le fichier sur disque n'existe pas (chemin /tmp/...jpg) : 404 attendu."""
    img_id = _img()
    rv = client.get(f"/api/image/{img_id}/file")
    assert rv.status_code == 404


def test_serve_image_unknown_id_returns_404(client):
    assert client.get("/api/image/9999/file").status_code == 404


# ─── Thumbnails (cache disque, generation a la demande) ──────────────────

def test_thumb_is_generated_and_cached(client, tmp_path):
    """1er appel : genere le fichier ; 2eme appel : sert depuis le cache."""
    import server
    img_id = _real_img(tmp_path, w=2000, h=2800)
    thumb_path = server.THUMB_DIR / "le_figaro_1930-05-25.jpg"
    assert not thumb_path.exists()

    rv = client.get(f"/api/image/{img_id}/thumb")
    assert rv.status_code == 200
    assert rv.mimetype == "image/jpeg"
    assert thumb_path.exists()

    # La thumb doit etre bien plus petite que l'original.
    src_size   = pathlib.Path(db.get_image(img_id)["path"]).stat().st_size
    thumb_size = thumb_path.stat().st_size
    assert thumb_size < src_size

    # Et redimensionnee a ~THUMB_WIDTH px de large.
    w, h = Image.open(thumb_path).size
    assert w == server.THUMB_WIDTH
    assert h < 2800  # ratio preserve

    # 2eme appel : cache hit -> meme mtime (pas de regeneration).
    mtime1 = thumb_path.stat().st_mtime_ns
    rv2 = client.get(f"/api/image/{img_id}/thumb")
    assert rv2.status_code == 200
    assert thumb_path.stat().st_mtime_ns == mtime1


def test_thumb_unknown_image_returns_404(client):
    assert client.get("/api/image/9999/thumb").status_code == 404


def test_thumb_404_when_source_file_missing(client):
    """L'entree DB existe mais le fichier source est absent."""
    img_id = _img()  # chemin /tmp/...jpg qui n'existe pas
    assert client.get(f"/api/image/{img_id}/thumb").status_code == 404
