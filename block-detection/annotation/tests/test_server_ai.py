"""Tests des routes restantes : crop/image, health, clientlog, suggestions,
propositions de correction, et les endpoints qui shellent (detect/review) ou
lancent un thread (replenish) — avec subprocess et threads MOCKÉS. Hermétique."""
from __future__ import annotations
import pytest
import db


class FakeProc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


@pytest.fixture
def fake_bloc_python(tmp_path, monkeypatch):
    """Fait croire au serveur que le python de bloc_detection existe (fichier bidon)."""
    import server
    exe = tmp_path / "python.exe"; exe.write_text("")
    monkeypatch.setattr(server, "BLOC_PYTHON", str(exe))
    return server


# ─────────────────────────── crop / image / health ──────────────────────

def test_serve_crop_ok(client, make_real_image):
    img_id = make_real_image(w=800, h=600)
    rv = client.get(f"/api/image/{img_id}/crop?box=0,0,200,150&w=100&h=100")
    assert rv.status_code == 200 and rv.mimetype == "image/jpeg"


def test_serve_crop_clamps_and_400_on_empty_box(client, make_real_image):
    img_id = make_real_image(w=800, h=600)
    # x1<x0 après parsing → région vide → 400
    rv = client.get(f"/api/image/{img_id}/crop?box=200,200,100,100")
    assert rv.status_code == 400


def test_serve_crop_400_on_malformed_box(client, make_real_image):
    img_id = make_real_image()
    assert client.get(f"/api/image/{img_id}/crop?box=abc").status_code == 400


def test_serve_crop_404_unknown_image(client):
    assert client.get("/api/image/9999/crop?box=0,0,1,1").status_code == 404


def test_serve_image_ok(client, make_real_image):
    img_id = make_real_image()
    rv = client.get(f"/api/image/{img_id}/file")
    assert rv.status_code == 200 and rv.mimetype == "image/jpeg"


def test_health_ok(client):
    rv = client.get("/api/health")
    assert rv.status_code == 200 and rv.get_json()["ok"] is True


def test_clientlog_writes_and_returns_204(client, tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, "PERF_LOG", tmp_path / "perf.log")
    rv = client.post("/api/clientlog", data="lag 200ms")
    assert rv.status_code == 204
    assert "lag 200ms" in (tmp_path / "perf.log").read_text(encoding="utf-8")


# ─────────────────────────── suggestions ────────────────────────────────

def test_get_suggestions_endpoint(client, make_image):
    i = make_image()
    db.replace_suggestions(i, [(0, 0, 10, 10, 0.9)])
    rv = client.get(f"/api/image/{i}/suggestions")
    assert rv.status_code == 200 and len(rv.get_json()) == 1


def test_apply_suggestions_endpoint_creates_annotations(client, make_image):
    i = make_image()
    lbl = db.list_labels()[0]["id"]
    db.replace_suggestions(i, [(1, 2, 3, 4, 0.9), (5, 6, 7, 8, 0.8)])
    rv = client.post(f"/api/image/{i}/apply-suggestions", json={"label_id": lbl})
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["created"] == 2 and len(body["ids"]) == 2
    assert len(db.list_annotations(i)) == 2
    assert db.list_suggestions(i) == []


# ─────────────────────────── propositions ───────────────────────────────

def _seed_proposal(image_id, before, after, ptype="reclassify"):
    db.replace_proposals(image_id, [{
        "ptype": ptype, "descr": "d",
        "payload": {"before": before, "after": after, "region": [0, 0, 10, 10]},
    }])
    return db.list_proposals(image_id)[0]["id"]


def test_get_proposals_endpoint(client, make_image):
    i = make_image()
    _seed_proposal(i, before=[], after=[])
    rv = client.get(f"/api/image/{i}/proposals")
    assert rv.status_code == 200 and len(rv.get_json()) == 1


def test_apply_proposal_deletes_before_and_creates_after(client, make_image):
    i = make_image()
    l1, l2 = db.list_labels()[:2]
    a_old = db.add_annotation(i, l1["id"], 0, 0, 10, 10)          # sera supprimée
    pid = _seed_proposal(
        i,
        before=[{"id": a_old, "label_id": l1["id"], "x0": 0, "y0": 0, "x1": 10, "y1": 10}],
        after=[{"label_id": l2["id"], "label_name": l2["name"], "x0": 5, "y0": 5, "x1": 20, "y1": 20}],
    )
    rv = client.post(f"/api/proposals/{pid}/apply")
    assert rv.status_code == 200
    body = rv.get_json()
    assert len(body["created"]) == 1 and len(body["deleted"]) == 1
    # l'ancienne est partie, la nouvelle est là avec le nouveau label
    annos = db.list_annotations(i)
    assert len(annos) == 1
    assert annos[0]["label_id"] == l2["id"]
    assert (annos[0]["x0"], annos[0]["y0"], annos[0]["x1"], annos[0]["y1"]) == (5, 5, 20, 20)
    # la proposition passe à accepted
    assert db.get_proposal(pid)["status"] == "accepted"


def test_apply_proposal_409_if_already_treated(client, make_image):
    i = make_image()
    pid = _seed_proposal(i, before=[], after=[])
    db.set_proposal_status(pid, "accepted")
    assert client.post(f"/api/proposals/{pid}/apply").status_code == 409


def test_apply_proposal_404_if_missing(client):
    assert client.post("/api/proposals/999999/apply").status_code == 404


def test_reject_proposal(client, make_image):
    i = make_image()
    pid = _seed_proposal(i, before=[], after=[])
    rv = client.post(f"/api/proposals/{pid}/reject")
    assert rv.status_code == 200
    assert db.get_proposal(pid)["status"] == "rejected"


def test_all_proposals_and_review_status(client, make_image):
    i = make_image("le_temps_1936-08-08", "Le Temps", "1936-08-08")
    _seed_proposal(i, before=[], after=[])
    assert len(client.get("/api/proposals/all").get_json()) == 1
    st = client.get("/api/review/status").get_json()
    assert st["pending"] == 1 and "reviewing" in st


# ────────────── endpoints qui shellent : subprocess MOCKÉ ────────────────

def test_detect_endpoint_ok(client, make_image, fake_bloc_python, monkeypatch):
    i = make_image()
    monkeypatch.setattr(fake_bloc_python.subprocess, "run", lambda *a, **k: FakeProc(0))
    rv = client.post(f"/api/image/{i}/detect", json={"conf": "0.4"})
    assert rv.status_code == 200 and "count" in rv.get_json()


def test_detect_endpoint_500_if_bloc_python_missing(client, make_image, monkeypatch):
    import server
    i = make_image()
    monkeypatch.setattr(server, "BLOC_PYTHON", "/chemin/inexistant/python.exe")
    rv = client.post(f"/api/image/{i}/detect", json={})
    assert rv.status_code == 500 and "error" in rv.get_json()


def test_detect_endpoint_404_unknown_image(client, fake_bloc_python):
    assert client.post("/api/image/9999/detect", json={}).status_code == 404


def test_detect_endpoint_reports_subprocess_failure(client, make_image, fake_bloc_python, monkeypatch):
    i = make_image()
    monkeypatch.setattr(fake_bloc_python.subprocess, "run",
                        lambda *a, **k: FakeProc(1, out="", err="boom"))
    rv = client.post(f"/api/image/{i}/detect", json={})
    assert rv.status_code == 500 and "boom" in rv.get_json()["detail"]


def test_review_compute_endpoint_ok(client, make_image, fake_bloc_python, monkeypatch):
    i = make_image()
    monkeypatch.setattr(fake_bloc_python.subprocess, "run", lambda *a, **k: FakeProc(0))
    rv = client.post(f"/api/image/{i}/review/compute")
    assert rv.status_code == 200 and "count" in rv.get_json()


# ────────────── endpoints qui lancent un thread : thread MOCKÉ ───────────

def test_review_compute_all_starts_and_returns(client, fake_bloc_python, monkeypatch):
    monkeypatch.setattr(fake_bloc_python, "_review_all_in_background", lambda: None)
    rv = client.post("/api/review/compute-all")
    assert rv.status_code == 200 and rv.get_json()["reviewing"] is True


def test_trigger_replenish_starts_and_returns(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "_replenish_in_background", lambda: None)
    rv = client.post("/api/replenish")
    assert rv.status_code == 200 and rv.get_json()["replenishing"] is True


def test_replenish_status_endpoint(client):
    rv = client.get("/api/replenish/status")
    assert rv.status_code == 200
    assert set(rv.get_json()) >= {"replenishing", "todo", "low_watermark"}


def test_corrections_page_renders(client):
    assert client.get("/corrections").status_code == 200
