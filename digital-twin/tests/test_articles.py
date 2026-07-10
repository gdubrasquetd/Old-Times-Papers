"""Tests du regroupement des blocs en articles. Purement géométrique/textuel :
aucun modèle, aucune image réelle (sauf un JPEG minuscule généré pour le rendu)."""
from __future__ import annotations
import base64
import io
import pytest
from PIL import Image

from blocks_util import paper_date
from articles import (_fold, is_masthead_noise, detect_columns, assign_columns,
                      merge_headline_fragments, group_articles, article_text,
                      render_articles_png)

W, H = 1200, 1000
COLW, PITCH = 180, 200          # 6 colonnes : x0 = 20, 220, 420, 620, 820, 1020


def blk(bid, cls, x0, y0, x1, y1, text="", conf=0.9):
    return {"id": bid, "class": cls, "conf": conf, "text": text,
            "box": [x0, y0, x1, y1]}


def body(bid, col, y0, y1, text="du texte de corps assez long pour ne pas etre filtre"):
    x0 = 20 + col * PITCH
    return blk(bid, "bloc de texte", x0, y0, x0 + COLW, y1, text)


# ─────────────────────────── _fold ──────────────────────────────────────

def test_fold_strips_accents_and_case():
    assert _fold("RÉDACTION Éàü") == "redaction eau"


def test_fold_none_is_empty():
    assert _fold(None) == ""


# ─────────────────────────── is_masthead_noise ──────────────────────────

def test_noise_header_and_illustration():
    assert is_masthead_noise(blk(0, "header", 0, 0, 10, 10, "x"), W, H)
    assert is_masthead_noise(blk(0, "illustration", 0, 500, 10, 510, ""), W, H)


def test_noise_empty_text():
    assert is_masthead_noise(blk(0, "bloc de texte", 0, 500, 10, 510, "  "), W, H)


def test_noise_strong_pattern_anywhere_on_page():
    # au milieu de la page, mais mention légale sans ambiguïté
    b = blk(0, "texte isolé", 0, 500, 400, 540, "Le Journal ne répond pas des manuscrits")
    assert is_masthead_noise(b, W, H)


def test_noise_topband_pattern_only_in_band():
    txt = "LES ANNONCES ET RÉCLAMES sont reçues au bureau du journal, rue Drouot"
    assert is_masthead_noise(blk(0, "texte isolé", 0, 100, 400, 130, txt), W, H)      # y≈0.115
    assert not is_masthead_noise(blk(0, "texte isolé", 0, 600, 400, 630, txt), W, H)  # y≈0.615


def test_noise_short_text_high_up_only():
    short = "SAMEDI 8 AOUT 1936"
    assert is_masthead_noise(blk(0, "texte isolé", 0, 60, 300, 90, short), W, H)      # y≈0.075
    assert not is_masthead_noise(blk(0, "texte isolé", 0, 300, 300, 330, short), W, H)


def test_real_article_is_not_noise():
    b = body(0, 0, 300, 500, "Le président du conseil a reçu ce matin les délégués du syndicat.")
    assert not is_masthead_noise(b, W, H)


# ─────────────────────────── detect_columns ─────────────────────────────

def test_detect_columns_six_bands():
    blocks = [body(i, i, 300, 500) for i in range(6)]
    cols = detect_columns(blocks, W)
    assert len(cols) == 6
    assert cols[0][0] == pytest.approx(20) and cols[0][1] == pytest.approx(200)


def test_detect_columns_two_bands():
    blocks = [body(0, 0, 300, 500), body(1, 1, 300, 500)]
    assert len(detect_columns(blocks, W)) == 2


def test_detect_columns_single_block():
    assert len(detect_columns([body(0, 0, 300, 500)], W)) == 1


def test_detect_columns_ignores_spanning_block():
    # un bloc à cheval (largeur 3x la médiane) ne doit pas créer/fusionner de colonne
    blocks = [body(i, i, 300, 500) for i in range(3)]
    blocks.append(blk(9, "bloc de texte", 20, 600, 20 + 3 * PITCH, 650, "large"))
    assert len(detect_columns(blocks, W)) == 3


def test_detect_columns_uses_only_bloc_de_texte():
    # un `texte isolé` à cheval sur 2 colonnes ne doit pas ponter les gouttières
    blocks = [body(0, 0, 300, 500), body(1, 1, 300, 500),
              blk(9, "texte isolé", 20, 600, 400, 650, "large")]
    assert len(detect_columns(blocks, W)) == 2


def test_detect_columns_empty():
    assert detect_columns([], W) == []


# ─────────────────────────── assign_columns ─────────────────────────────

COLS = [(20 + i * PITCH, 20 + i * PITCH + COLW) for i in range(6)]


def test_assign_columns_narrow_block_single_column():
    assert assign_columns([30, 0, 190, 10], COLS) == (0, 0)


def test_assign_columns_banner_spans_several():
    assert assign_columns([220, 0, 780, 10], COLS) == (1, 3)


def test_assign_columns_tiny_block_falls_back_to_center():
    # trop étroit pour couvrir 50 % d'une colonne -> colonne de son centre
    assert assign_columns([500, 0, 540, 10], COLS) == (2, 2)


def test_assign_columns_no_columns():
    assert assign_columns([0, 0, 10, 10], []) == (0, 0)


# ─────────────────────────── merge_headline_fragments ───────────────────

def test_merge_stacked_headline_fragments():
    t1 = blk(0, "titre", 20, 100, 200, 140, "LA GUERRE")
    t2 = blk(1, "titre", 20, 145, 200, 180, "en Espagne")
    groups = merge_headline_fragments([t1, t2], COLS, H)
    assert len(groups) == 1
    assert groups[0]["text"] == "LA GUERRE\nen Espagne"
    assert groups[0]["box"] == [20, 100, 200, 180]


def test_merge_keeps_distant_headlines_separate():
    t1 = blk(0, "titre", 20, 100, 200, 140, "TITRE A")
    t2 = blk(1, "titre", 20, 400, 200, 440, "TITRE B")     # écart vertical > 2 % de H
    assert len(merge_headline_fragments([t1, t2], COLS, H)) == 2


def test_merge_drops_headline_line_contained_in_another():
    # l'OCR détecte deux fois le même titre, l'une des lectures étant tronquée
    t1 = blk(0, "titre", 20, 100, 200, 140, "Mesures de précaution")
    t2 = blk(1, "titre", 20, 145, 200, 180,
             "Mesures de précaution contre le bombardement de la capitale")
    g = merge_headline_fragments([t1, t2], COLS, H)[0]
    assert g["text"] == "Mesures de précaution contre le bombardement de la capitale"


def test_merge_drops_exact_duplicate_lines():
    t1 = blk(0, "titre", 20, 100, 200, 140, "EN ARAGON")
    t2 = blk(1, "titre", 20, 145, 200, 180, "En Aragon")     # même ligne, casse différente
    assert merge_headline_fragments([t1, t2], COLS, H)[0]["text"] == "EN ARAGON"


def test_merge_keeps_genuinely_different_lines():
    t1 = blk(0, "titre", 20, 100, 200, 140, "LA GUERRE CIVILE EN ESPAGNE")
    t2 = blk(1, "titre", 20, 145, 200, 180, "Les insurgés vont reprendre l'offensive")
    g = merge_headline_fragments([t1, t2], COLS, H)[0]
    assert g["text"] == "LA GUERRE CIVILE EN ESPAGNE\nLes insurgés vont reprendre l'offensive"


def test_merge_keeps_different_column_spans_separate():
    t1 = blk(0, "titre", 20, 100, 200, 140, "COL0")
    t2 = blk(1, "titre", 220, 100, 400, 140, "COL1")
    assert len(merge_headline_fragments([t1, t2], COLS, H)) == 2


# ─────────────────────────── group_articles ─────────────────────────────

def test_group_two_columns_two_articles():
    blocks = [blk(0, "titre", 20, 200, 200, 240, "Titre A"),
              body(1, 0, 250, 400),
              blk(2, "titre", 220, 200, 400, 240, "Titre B"),
              body(3, 1, 250, 400)]
    g = group_articles(blocks, W, H)
    assert len(g["articles"]) == 2
    heads = {a["headline"] for a in g["articles"]}
    assert heads == {"Titre A", "Titre B"}
    a = next(x for x in g["articles"] if x["headline"] == "Titre A")
    assert a["block_ids"] == [1]


def test_group_banner_headline_owns_both_columns():
    blocks = [blk(0, "titre", 20, 150, 400, 190, "GRAND TITRE QUI BARRE DEUX COLONNES"),
              body(1, 0, 250, 400), body(2, 1, 250, 400)]
    g = group_articles(blocks, W, H)
    assert len(g["articles"]) == 1
    a = g["articles"][0]
    assert a["columns"] == [0, 1]
    assert sorted(a["block_ids"]) == [1, 2]


def test_group_orphan_body_becomes_breve():
    blocks = [body(0, 0, 150, 190),                       # au-dessus de tout titre
              blk(1, "titre", 20, 200, 200, 240, "Titre A"),
              body(2, 0, 250, 400)]
    g = group_articles(blocks, W, H)
    breves = [a for a in g["articles"] if not a["headline"]]
    assert len(breves) == 1 and breves[0]["block_ids"] == [0]


def test_group_drops_noise_blocks():
    blocks = [blk(0, "texte isolé", 0, 60, 300, 90, "PRIX DE L'ABONNEMENT"),
              blk(1, "titre", 20, 200, 200, 240, "Titre A"),
              body(2, 0, 250, 400)]
    g = group_articles(blocks, W, H)
    assert g["dropped"] == [0]
    assert all(0 not in a["block_ids"] for a in g["articles"])


def test_group_assigns_sequential_ids_in_reading_order():
    blocks = [blk(0, "titre", 220, 200, 400, 240, "Bas droite"),
              body(1, 1, 250, 400),
              blk(2, "titre", 20, 150, 200, 190, "Haut gauche"),
              body(3, 0, 200, 400)]
    g = group_articles(blocks, W, H)
    assert [a["id"] for a in g["articles"]] == [0, 1]
    assert g["articles"][0]["headline"] == "Haut gauche"   # plus haut d'abord


# ─────────────────────────── article_text ───────────────────────────────

def test_article_text_joins_in_reading_order():
    a = {"blocks": [body(0, 0, 0, 10, "premier"), body(1, 0, 20, 30, "second")]}
    assert article_text(a) == "premier\nsecond"


def test_article_text_skips_empty_blocks():
    a = {"blocks": [body(0, 0, 0, 10, "seul"), body(1, 0, 20, 30, "  ")]}
    assert article_text(a) == "seul"


# ─────────────────────────── render_articles_png ────────────────────────

def test_render_articles_png_writes_decodable_png(tmp_path):
    jpg = tmp_path / "une.jpg"
    Image.new("RGB", (W, H), (240, 235, 220)).save(jpg)
    blocks = [blk(0, "titre", 20, 200, 200, 240, "Titre A"), body(1, 0, 250, 400)]
    data = {"slug": "le_temps_1936-08-08", "image": str(jpg),
            "img_w": W, "img_h": H, "blocks": blocks}
    grouped = group_articles(blocks, W, H)
    out = tmp_path / "articles.png"
    render_articles_png(data, grouped, out, target_h=200)
    assert Image.open(out).format == "PNG"


# ─────────────────────────── paper_date (blocks_util) ───────────────────

def test_paper_date_extracts_iso_date():
    assert paper_date("le_temps_1936-08-08") == "1936-08-08"


def test_paper_date_none_when_absent():
    assert paper_date("le_temps") is None
