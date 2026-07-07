"""Tests de la logique de dédoublonnage des suggestions (pur, sans modèle)."""
from __future__ import annotations
import pytest
from suggest import _overlap_min, suppress_overlaps


def test_overlap_min_containment_is_one():
    big = (0, 0, 100, 100)
    small = (10, 10, 20, 20)          # entièrement dans big
    assert _overlap_min(big, small) == pytest.approx(1.0)


def test_overlap_min_disjoint_is_zero():
    assert _overlap_min((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_overlap_min_partial():
    # A=(0,0,10,10) aire 100 ; B=(5,0,15,10) aire 100 ; inter = 5x10 = 50
    assert _overlap_min((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(0.5)


def test_suppress_overlaps_keeps_highest_conf():
    boxes = [(0, 0, 10, 10, 0.9), (1, 1, 9, 9, 0.5)]      # recouvrement fort
    kept, removed = suppress_overlaps(boxes, thresh=0.5)
    assert removed == 1
    assert kept == [(0, 0, 10, 10, 0.9)]                 # la plus confiante gardée


def test_suppress_overlaps_keeps_disjoint_boxes():
    boxes = [(0, 0, 10, 10, 0.9), (20, 20, 30, 30, 0.8)]
    kept, removed = suppress_overlaps(boxes, thresh=0.5)
    assert removed == 0 and len(kept) == 2


def test_suppress_overlaps_threshold_respected():
    # recouvrement = 0.5 ; avec un seuil à 0.6 on NE supprime PAS
    boxes = [(0, 0, 10, 10, 0.9), (5, 0, 15, 10, 0.5)]
    kept, removed = suppress_overlaps(boxes, thresh=0.6)
    assert removed == 0 and len(kept) == 2
