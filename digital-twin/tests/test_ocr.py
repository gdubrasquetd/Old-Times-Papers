"""Tests de la logique pure d'ocr.py : dé-césure (post-traitement clé) et cadrage
des crops. Aucun modèle OCR n'est chargé (les runners importent torch/pero
paresseusement)."""
from __future__ import annotations
from PIL import Image
from ocr import dehyphenate, crop_of, ROUTE, TEXT


# ─────────────────────────── dehyphenate ────────────────────────────────

def test_dehyphenate_rejoins_word_across_newline():
    assert dehyphenate("l'auto-\nrité") == "l'autorité"


def test_dehyphenate_handles_notch_glyph():
    # ¬ (U+00AC), césure typographique fréquente dans l'OCR de presse ancienne
    assert dehyphenate("no¬\ntamment") == "notamment"


def test_dehyphenate_handles_soft_hyphen():
    assert dehyphenate("ré­\nsultat") == "résultat"


def test_dehyphenate_keeps_real_compound_hyphen():
    # tiret de mot composé en milieu de ligne -> conservé
    assert dehyphenate("grand-père arrive") == "grand-père arrive"


def test_dehyphenate_does_not_join_capitalized_continuation():
    # une majuscule après le tiret n'est pas une césure (ex. Nord-\nEst)
    assert dehyphenate("Nord-\nEst") == "Nord-\nEst"


def test_dehyphenate_absorbs_surrounding_spaces():
    assert dehyphenate("auto- \n rité") == "autorité"


def test_dehyphenate_multiple_occurrences():
    assert dehyphenate("con-\ntre et sui-\nvant") == "contre et suivant"


def test_dehyphenate_none_and_empty():
    assert dehyphenate(None) == ""
    assert dehyphenate("") == ""


# ─────────────────────────── crop_of ────────────────────────────────────

def test_crop_of_applies_padding_inside_bounds():
    im = Image.new("RGB", (100, 100))
    # PAD=6 : (40,40,60,60) -> (34,34,66,66) = 32x32
    c = crop_of(im, {"box": [40, 40, 60, 60]})
    assert c.size == (32, 32)


def test_crop_of_clamps_to_image_edges():
    im = Image.new("RGB", (100, 100))
    # box qui touche/dépasse les bords -> clampé à [0,100]
    c = crop_of(im, {"box": [95, 95, 200, 200]})
    assert c.size == (11, 11)   # x0=max(0,89)=89..100, y idem


# ─────────────────────────── routage figé ───────────────────────────────

def test_route_sends_all_text_classes_to_pero():
    assert set(ROUTE) == TEXT
    assert set(ROUTE.values()) == {"pero"}


def test_header_and_illustration_are_not_text_classes():
    assert "header" not in TEXT and "illustration" not in TEXT
