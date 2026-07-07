"""Tests de la logique pure de build.py (jumeau numérique) : nom du journal,
échappement HTML, dédoublonnage de blocs superposés, encodage des crops."""
from __future__ import annotations
import base64
import io
from PIL import Image
from build import paper_name, esc, dedup_blocks, crop_b64


# ─────────────────────────── paper_name ─────────────────────────────────

def test_paper_name_known_slug_with_date():
    assert paper_name("le_temps_1936-08-08") == "Le Temps"


def test_paper_name_apostrophe_title():
    assert paper_name("intransigeant_1914-07-31") == "L'Intransigeant"


def test_paper_name_unknown_falls_back_to_titlecase():
    assert paper_name("gazette_locale_1900-01-01") == "Gazette Locale"


# ─────────────────────────── esc ────────────────────────────────────────

def test_esc_escapes_html_specials():
    assert esc("<b> AT&T </b>") == "&lt;b&gt; AT&amp;T &lt;/b&gt;"


def test_esc_none_is_empty():
    assert esc(None) == ""


# ─────────────────────────── dedup_blocks ───────────────────────────────

def _b(box, text, conf=0.5):
    return {"box": box, "text": text, "conf": conf}


def test_dedup_removes_overlapping_same_text_keeps_best_conf():
    blocks = [_b([0, 0, 100, 100], "Bonjour", 0.9),
              _b([2, 2, 98, 98], "bonjour", 0.5)]   # ~92% IoU, même texte (à la casse près)
    kept, n = dedup_blocks(blocks)
    assert n == 1 and len(kept) == 1
    assert kept[0]["conf"] == 0.9                    # on garde le plus confiant


def test_dedup_keeps_blocks_with_different_text():
    blocks = [_b([0, 0, 100, 100], "chat"), _b([2, 2, 98, 98], "chien")]
    kept, n = dedup_blocks(blocks)
    assert n == 0 and len(kept) == 2


def test_dedup_keeps_disjoint_boxes_same_text():
    blocks = [_b([0, 0, 10, 10], "titre"), _b([500, 500, 510, 510], "titre")]
    kept, n = dedup_blocks(blocks)
    assert n == 0 and len(kept) == 2                 # IoU ~0 -> pas de dédup


def test_dedup_ignores_empty_text():
    blocks = [_b([0, 0, 100, 100], ""), _b([0, 0, 100, 100], None)]
    kept, n = dedup_blocks(blocks)
    assert n == 0 and len(kept) == 2                 # texte vide -> jamais considéré doublon


def test_dedup_threshold_respected():
    # IoU ~0.33 (recouvrement partiel) < 0.6 -> conservés tous les deux
    blocks = [_b([0, 0, 100, 100], "x"), _b([50, 0, 150, 100], "x")]
    kept, n = dedup_blocks(blocks)
    assert n == 0 and len(kept) == 2


# ─────────────────────────── crop_b64 ───────────────────────────────────

def test_crop_b64_returns_decodable_jpeg():
    im = Image.new("RGB", (200, 100), (200, 30, 30))
    s = crop_b64(im, (0, 0, 100, 100))
    img = Image.open(io.BytesIO(base64.b64decode(s)))
    assert img.format == "JPEG" and img.size == (100, 100)


def test_crop_b64_downscales_wide_crop_to_maxw():
    im = Image.new("RGB", (2000, 1000))
    s = crop_b64(im, (0, 0, 2000, 1000), maxw=680)
    img = Image.open(io.BytesIO(base64.b64decode(s)))
    assert img.width == 680 and img.height == 340   # ratio préservé
