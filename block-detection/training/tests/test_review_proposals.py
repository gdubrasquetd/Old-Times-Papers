"""Tests de la génération de propositions de correction (logique pure, sans
modèle). On fabrique GT et prédictions à la main."""
from __future__ import annotations
import pytest
from review_proposals import iou, inside_ratio, build_proposals, pad_region

ID_BY_NAME = {"titre": 1, "bloc de texte": 2, "illustration": 3, "texte isolé": 4}


def _gt(gid, name, box, label_id=None):
    x0, y0, x1, y1 = box
    return {"id": gid, "label_name": name, "label_id": label_id or ID_BY_NAME[name],
            "x0": x0, "y0": y0, "x1": x1, "y1": y1}


# ─────────────────────────── iou / inside_ratio ─────────────────────────

def test_iou_identical():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)


def test_iou_disjoint():
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_inside_ratio_fully_inside():
    assert inside_ratio((10, 10, 20, 20), (0, 0, 100, 100)) == pytest.approx(1.0)


def test_inside_ratio_half():
    assert inside_ratio((0, 0, 10, 10), (5, 0, 100, 100)) == pytest.approx(0.5)


def test_pad_region_clamps_to_image():
    # pad=70 déborderait en négatif / au-delà de W,H -> clampé
    assert pad_region([(10, 10, 20, 20)], 100, 100, pad=70) == [0, 0, 90, 90]


# ─────────────────────────── build_proposals ────────────────────────────

def test_split_title_when_title_inside_host():
    host = _gt(1, "bloc de texte", (0, 0, 200, 300))
    preds = [("titre", 10, 100, 190, 140, 0.9)]           # titre au milieu du bloc
    props = build_proposals([host], preds, ID_BY_NAME, 1000, 800)
    assert len(props) == 1 and props[0]["ptype"] == "split_title"
    after = props[0]["payload"]["after"]
    assert any(a["label_name"] == "titre" for a in after)         # le titre séparé
    assert any(a["label_name"] == "bloc de texte" for a in after)  # + morceaux de texte
    assert props[0]["payload"]["before"][0]["id"] == 1            # l'hôte est retiré


def test_add_unannotated_title_outside_hosts():
    preds = [("titre", 500, 500, 600, 540, 0.9)]          # aucun bloc hôte
    props = build_proposals([], preds, ID_BY_NAME, 1000, 800)
    assert len(props) == 1 and props[0]["ptype"] == "split_title"
    assert props[0]["payload"]["before"] == []
    assert props[0]["payload"]["after"][0]["label_name"] == "titre"


def test_reclassify_when_gt_matches_other_class():
    gt = _gt(5, "titre", (0, 0, 100, 100))
    preds = [("illustration", 0, 0, 100, 100, 0.9)]       # même endroit, autre classe
    props = build_proposals([gt], preds, ID_BY_NAME, 1000, 800)
    assert len(props) == 1 and props[0]["ptype"] == "reclassify"
    assert props[0]["payload"]["after"][0]["label_name"] == "illustration"
    assert props[0]["payload"]["before"][0]["id"] == 5


def test_no_split_when_title_already_annotated():
    gt_titre = _gt(7, "titre", (10, 100, 190, 140))
    host = _gt(1, "bloc de texte", (0, 0, 200, 300))
    preds = [("titre", 10, 100, 190, 140, 0.9)]           # correspond au titre déjà annoté
    props = build_proposals([gt_titre, host], preds, ID_BY_NAME, 1000, 800)
    assert all(p["ptype"] != "split_title" for p in props)


def test_no_reclassify_when_same_class():
    gt = _gt(3, "titre", (0, 0, 100, 100))
    preds = [("titre", 0, 0, 100, 100, 0.9)]              # même classe -> rien à corriger
    props = build_proposals([gt], preds, ID_BY_NAME, 1000, 800)
    assert props == []


def test_low_confidence_prediction_ignored():
    gt = _gt(3, "titre", (0, 0, 100, 100))
    preds = [("illustration", 0, 0, 100, 100, 0.10)]      # conf < CONF_RECLASS
    props = build_proposals([gt], preds, ID_BY_NAME, 1000, 800)
    assert props == []
