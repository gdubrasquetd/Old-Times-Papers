"""Tests des métriques du grand comparatif (norm / CER / WER) et du post-traitement
de césures. Ce sont ces fonctions qui fondent la conclusion « PERO gagne » — leur
justesse est donc critique. Déterministe et hors-ligne (rapidfuzz uniquement)."""
from __future__ import annotations
import pytest
from eval_lib import norm, cer, wer, pp_cesures, pp_brut


# ─────────────────────────── norm ───────────────────────────────────────

def test_norm_lowercases_collapses_spaces_and_strips():
    assert norm("  Le  Petit\n Journal \t") == "le petit journal"


def test_norm_unifies_typographic_apostrophe_and_quotes():
    assert norm("L’État") == "l'état"           # ’ -> '
    assert norm("«oui»") == '"oui"'             # « » -> "


def test_norm_none_is_empty():
    assert norm(None) == ""


# ─────────────────────────── cer ────────────────────────────────────────

def test_cer_identical_is_zero():
    assert cer("bonjour", "bonjour") == 0.0


def test_cer_single_substitution():
    # "bonjour" vs "bonjonr" : 1 caractère faux sur 7
    assert cer("bonjour", "bonjonr") == pytest.approx(1 / 7)


def test_cer_ignores_case_and_spacing_via_norm():
    assert cer("Le Temps", "le   temps") == 0.0


def test_cer_empty_reference_returns_none():
    assert cer("", "quelque chose") is None


# ─────────────────────────── wer ────────────────────────────────────────

def test_wer_identical_is_zero():
    assert wer("le chat dort", "le chat dort") == 0.0


def test_wer_one_word_wrong_over_three():
    assert wer("le chat dort", "le chien dort") == pytest.approx(1 / 3)


def test_wer_empty_reference_returns_none():
    assert wer("   ", "des mots") is None


# ─────────────────────────── post-traitements ───────────────────────────

def test_pp_cesures_rejoins_hyphenated_word():
    assert pp_cesures("auto-\nrité") == "autorité"


def test_pp_cesures_keeps_compound_hyphen():
    assert pp_cesures("grand-père") == "grand-père"


def test_pp_brut_is_identity_and_none_safe():
    assert pp_brut("abc") == "abc"
    assert pp_brut(None) == ""
