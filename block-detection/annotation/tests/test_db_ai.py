"""Tests DB des fonctionnalités d'assistance (suggestions du détecteur,
propositions de correction) + count_by_journal + list_images. Tout hermétique
(DB temporaire fournie par la fixture autouse `isolated_db`)."""
from __future__ import annotations
import db


def _img(slug="le_figaro_1930-05-25", journal="Le Figaro", date="1930-05-25"):
    return db.add_image(slug, journal, date, "cb1", None, f"/tmp/{slug}.jpg", 100, 200)


# ─────────────────────────── count_by_journal ───────────────────────────

def test_count_by_journal_groups_all():
    _img("a", "Le Figaro"); _img("b", "Le Figaro"); _img("c", "Le Temps")
    assert db.count_by_journal() == {"Le Figaro": 2, "Le Temps": 1}


def test_count_by_journal_filters_by_status():
    f1 = _img("a", "Le Figaro"); _img("b", "Le Figaro"); _img("c", "Le Temps")
    db.set_image_status(f1, "done")
    assert db.count_by_journal(status="done") == {"Le Figaro": 1}


def test_count_by_journal_empty_when_no_images():
    assert db.count_by_journal() == {}


# ─────────────────────────── list_images ────────────────────────────────

def test_list_images_counts_annotations():
    i = _img()
    lbl = db.list_labels()[0]["id"]
    db.add_annotation(i, lbl, 0, 0, 1, 1)
    db.add_annotation(i, lbl, 2, 2, 3, 3)
    [row] = db.list_images()
    assert row["n_annotations"] == 2


def test_list_images_filter_by_status():
    a = _img("a"); _img("b")
    db.set_image_status(a, "done")
    done = db.list_images(status="done")
    assert [r["slug"] for r in done] == ["a"]


# ─────────────────────────── suggestions ────────────────────────────────

def test_replace_suggestions_returns_count_and_persists():
    i = _img()
    n = db.replace_suggestions(i, [(0, 0, 10, 10, 0.9), (5, 5, 20, 20, 0.5)], model="m1")
    assert n == 2
    assert len(db.list_suggestions(i)) == 2


def test_replace_suggestions_wipes_previous():
    i = _img()
    db.replace_suggestions(i, [(0, 0, 1, 1, 0.9)])
    db.replace_suggestions(i, [(0, 0, 2, 2, 0.8), (0, 0, 3, 3, 0.7)])
    assert len(db.list_suggestions(i)) == 2      # pas 3 : les anciennes ont été virées


def test_list_suggestions_ordered_by_conf_desc():
    i = _img()
    db.replace_suggestions(i, [(0, 0, 1, 1, 0.2), (0, 0, 1, 1, 0.9), (0, 0, 1, 1, 0.5)])
    confs = [round(s["conf"], 1) for s in db.list_suggestions(i)]
    assert confs == [0.9, 0.5, 0.2]


def test_apply_suggestions_materialises_and_clears():
    i = _img()
    lbl = db.list_labels()[0]["id"]
    db.replace_suggestions(i, [(1, 2, 3, 4, 0.9), (5, 6, 7, 8, 0.8)])
    ids = db.apply_suggestions(i, lbl)
    # renvoie les ids créés
    assert len(ids) == 2
    # les suggestions sont devenues des annotations avec le label donné
    annos = db.list_annotations(i)
    assert {(a["x0"], a["y0"], a["x1"], a["y1"]) for a in annos} == {(1, 2, 3, 4), (5, 6, 7, 8)}
    assert all(a["label_id"] == lbl for a in annos)
    # et les suggestions ont été effacées
    assert db.list_suggestions(i) == []


def test_suggestions_cascade_on_image_delete():
    i = _img()
    db.replace_suggestions(i, [(0, 0, 1, 1, 0.5)])
    conn = db.get_conn()
    conn.execute("DELETE FROM images WHERE id = ?", (i,))
    conn.commit(); conn.close()
    assert db.list_suggestions(i) == []


# ─────────────────────────── proposals ──────────────────────────────────

def _payload(before=None, after=None):
    return {"before": before or [], "after": after or [], "region": [0, 0, 10, 10]}


def test_replace_proposals_returns_count_and_decodes_payload():
    i = _img()
    n = db.replace_proposals(i, [
        {"ptype": "reclassify", "descr": "d1", "payload": _payload(after=[{"label_id": 1}])},
    ])
    assert n == 1
    [p] = db.list_proposals(i)
    assert p["ptype"] == "reclassify"
    assert p["payload"]["after"] == [{"label_id": 1}]     # JSON round-trip OK


def test_replace_proposals_only_wipes_pending():
    i = _img()
    db.replace_proposals(i, [{"ptype": "reclassify", "descr": "", "payload": _payload()}])
    [p] = db.list_proposals(i)
    db.set_proposal_status(p["id"], "accepted")           # celle-ci ne doit pas être virée
    db.replace_proposals(i, [{"ptype": "split_title", "descr": "", "payload": _payload()}])
    assert db.count_proposals(i, status="pending") == 1
    assert db.count_proposals(i, status="accepted") == 1


def test_get_proposal_decodes_and_none_if_missing():
    i = _img()
    db.replace_proposals(i, [{"ptype": "reclassify", "descr": "", "payload": _payload(after=[{"x": 1}])}])
    pid = db.list_proposals(i)[0]["id"]
    assert db.get_proposal(pid)["payload"]["after"] == [{"x": 1}]
    assert db.get_proposal(999999) is None


def test_set_proposal_status_transition():
    i = _img()
    db.replace_proposals(i, [{"ptype": "reclassify", "descr": "", "payload": _payload()}])
    pid = db.list_proposals(i)[0]["id"]
    db.set_proposal_status(pid, "rejected")
    assert db.list_proposals(i, status="pending") == []
    assert db.list_proposals(i, status="rejected")[0]["id"] == pid


def test_count_proposals_by_status():
    i = _img()
    db.replace_proposals(i, [
        {"ptype": "reclassify", "descr": "", "payload": _payload()},
        {"ptype": "split_title", "descr": "", "payload": _payload()},
    ])
    assert db.count_proposals(i) == 2                       # pending par défaut
    assert db.count_proposals(i, status="accepted") == 0


def test_list_all_proposals_joins_image_info():
    i = _img("le_temps_1936-08-08", "Le Temps", "1936-08-08")
    db.replace_proposals(i, [{"ptype": "reclassify", "descr": "", "payload": _payload()}])
    [p] = db.list_all_proposals("pending")
    assert p["slug"] == "le_temps_1936-08-08"
    assert p["journal"] == "Le Temps"
    assert p["iso_date"] == "1936-08-08"
