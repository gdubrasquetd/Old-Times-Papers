"""Tests des parties pures de summarize.py : prompts, parsing JSON tolérant,
normalisation des thèmes, replis. Aucun modèle n'est chargé — `generate` est injecté."""
from __future__ import annotations
import json
import pytest

from summarize import (TAXONOMY, build_article_prompt, build_global_prompt,
                       coerce_summary, first_sentence, normalize_themes,
                       parse_llm_json, summarize_article, summarize_global,
                       summarize_page, MAX_SUMMARY_CHARS)


# ─────────────────────────── parse_llm_json ─────────────────────────────

def test_parse_plain_json():
    assert parse_llm_json('{"summary": "ok"}') == {"summary": "ok"}


def test_parse_fenced_json():
    raw = '```json\n{"summary": "ok", "themes": []}\n```'
    assert parse_llm_json(raw)["summary"] == "ok"


def test_parse_json_with_prose_around():
    raw = 'Voici le résultat :\n{"summary": "ok"}\nJ\'espère que cela convient.'
    assert parse_llm_json(raw) == {"summary": "ok"}


def test_parse_tolerates_trailing_comma():
    assert parse_llm_json('{"summary": "ok", "themes": ["guerre"],}')["themes"] == ["guerre"]


def test_parse_handles_braces_inside_string():
    raw = '{"summary": "il a dit { non }", "themes": []}'
    assert parse_llm_json(raw)["summary"] == "il a dit { non }"


def test_parse_nested_object():
    assert parse_llm_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_parse_garbage_returns_none():
    assert parse_llm_json("je ne sais pas répondre") is None
    assert parse_llm_json("") is None
    assert parse_llm_json(None) is None


def test_parse_json_array_returns_none():
    assert parse_llm_json('[1, 2, 3]') is None      # on veut un objet, pas un tableau


# ─────────────────────────── normalize_themes ───────────────────────────

def test_normalize_maps_synonym():
    themes, _ = normalize_themes(["Diplomatie"])
    assert themes == ["politique étrangère"]


def test_normalize_is_accent_and_case_insensitive():
    themes, _ = normalize_themes(["POLITIQUE ETRANGERE", "Économie"])
    assert themes == ["politique étrangère", "économie"]


def test_normalize_unknown_theme_becomes_keyword():
    themes, kws = normalize_themes(["aviation"], ["blériot"])
    assert themes == []
    assert "aviation" in kws and "blériot" in kws


def test_normalize_dedups_and_orders_by_taxonomy():
    themes, _ = normalize_themes(["guerre", "politique intérieure", "guerre"])
    assert themes == ["politique intérieure", "guerre"]     # ordre de TAXONOMY


def test_normalize_themes_subset_of_taxonomy():
    themes, _ = normalize_themes(["justice", "n'importe quoi"])
    assert set(themes) <= set(TAXONOMY)


def test_normalize_clamps_themes_to_three():
    # le modèle a tendance à cocher toute la taxonomie -> on garde les 3 premiers
    themes, _ = normalize_themes(list(TAXONOMY))
    assert themes == TAXONOMY[:3]


def test_normalize_clamps_keywords():
    themes, kws = normalize_themes([], [f"mot{i}" for i in range(20)])
    assert len(kws) == 8


def test_normalize_ignores_non_strings():
    themes, kws = normalize_themes([None, 3, "guerre"], [None, "paix"])
    assert themes == ["guerre"] and kws == ["paix"]


# ─────────────────────────── coerce_summary ─────────────────────────────

def test_coerce_fills_missing_keys():
    assert coerce_summary({}) == {"summary": "", "themes": [], "keywords": []}


def test_coerce_clamps_long_summary():
    out = coerce_summary({"summary": "a" * 1000})
    assert len(out["summary"]) == MAX_SUMMARY_CHARS


def test_coerce_collapses_whitespace():
    assert coerce_summary({"summary": " deux   mots\n"})["summary"] == "deux mots"


def test_coerce_wrong_types_are_ignored():
    out = coerce_summary({"summary": ["pas", "une", "chaine"], "themes": "guerre",
                          "keywords": 42})
    assert out["summary"] == "" and out["themes"] == [] and out["keywords"] == []


def test_coerce_non_dict_input():
    assert coerce_summary("bruit") == {"summary": "", "themes": [], "keywords": []}


# ─────────────────────────── first_sentence ─────────────────────────────

def test_first_sentence_stops_at_punctuation():
    assert first_sentence("Le ministre a parlé. Puis il est parti.") == "Le ministre a parlé."


def test_first_sentence_without_punctuation():
    assert first_sentence("un texte sans point") == "un texte sans point"


def test_first_sentence_empty():
    assert first_sentence("") == "" and first_sentence(None) == ""


# ─────────────────────────── prompts ────────────────────────────────────

def test_article_prompt_truncates_body():
    p = build_article_prompt("Titre", "x" * 5000, max_chars=100)
    assert "x" * 100 in p and "x" * 101 not in p


def test_article_prompt_contains_taxonomy_and_headline():
    p = build_article_prompt("LA GUERRE", "corps")
    assert "LA GUERRE" in p
    for t in TAXONOMY:
        assert t in p


def test_article_prompt_strict_adds_instruction():
    assert "UNIQUEMENT" in build_article_prompt("t", "b", strict=True)
    assert "UNIQUEMENT" not in build_article_prompt("t", "b")


def test_article_prompt_handles_missing_headline():
    assert "(sans titre)" in build_article_prompt("", "corps")


def test_global_prompt_includes_every_summary():
    p = build_global_prompt(["résumé un", "résumé deux"], "1936-08-08")
    assert "résumé un" in p and "résumé deux" in p and "1936-08-08" in p


def test_global_prompt_omits_paper_name():
    """Régression : quand le nom du journal figurait dans le prompt, le modèle
    le recopiait tel quel comme résumé global (« Le Temps »)."""
    p = build_global_prompt(["un résumé"], "1936-08-08")
    assert "Le Temps" not in p and "journal" not in p.lower()


def test_global_prompt_truncates_each_summary():
    # sinon 19 résumés × 400 car. font déborder la fenêtre de contexte
    p = build_global_prompt(["x" * 500], "1936", item_chars=50)
    assert "x" * 50 in p and "x" * 51 not in p


def test_global_prompt_caps_number_of_items():
    p = build_global_prompt([f"resume{i}" for i in range(30)], "1936", max_items=5)
    assert "resume4" in p and "resume5" not in p


def test_global_prompt_skips_empty_summaries():
    p = build_global_prompt(["", "   ", "vrai"], "1936")
    assert "1. vrai" in p


def test_global_prompt_without_date():
    assert "None" not in build_global_prompt(["a"], None)


# ─────────────────────────── summarize_article (generate injecté) ───────

def _gen_ok(prompt):
    return '{"summary": "Un résumé.", "themes": ["Diplomatie"], "keywords": ["madrid"]}'


def test_summarize_article_happy_path():
    out = summarize_article(_gen_ok, "Titre", "corps")
    assert out["summary"] == "Un résumé."
    assert out["themes"] == ["politique étrangère"]
    assert out["degraded"] is False


def test_summarize_article_retries_once_then_succeeds():
    calls = []

    def gen(prompt):
        calls.append(prompt)
        return "pas du json" if len(calls) == 1 else _gen_ok(prompt)

    out = summarize_article(gen, "Titre", "corps")
    assert len(calls) == 2
    assert "UNIQUEMENT" in calls[1]          # le 2e appel est le prompt strict
    assert out["degraded"] is False


def test_summarize_article_falls_back_to_extractive():
    out = summarize_article(lambda p: "jamais du json", "Titre",
                            "Première phrase. Deuxième phrase.")
    assert out["summary"] == "Première phrase."
    assert out["themes"] == [] and out["degraded"] is True


def test_summarize_global_falls_back_without_crashing():
    out = summarize_global(lambda p: "bruit", ["a", "b"], "1936-08-08")
    assert out["degraded"] is True and "a" in out["summary"]


# ─────────────────────────── summarize_page ─────────────────────────────

def _page():
    long_text = ("Le président du conseil a reçu ce matin les délégués du syndicat "
                 "des mineurs afin d'examiner les revendications salariales. " * 2)
    blocks = [
        {"id": 0, "class": "titre", "conf": .9, "box": [20, 200, 200, 240],
         "text": "GREVE DES MINEURS"},
        {"id": 1, "class": "bloc de texte", "conf": .9, "box": [20, 250, 200, 400],
         "text": long_text},
    ]
    return {"slug": "le_temps_1936-08-08", "img_w": 1200, "img_h": 1000, "blocks": blocks}


def test_summarize_page_shape_and_metadata():
    out = summarize_page(_page(), _gen_ok, model_name="fake")
    assert out["slug"] == "le_temps_1936-08-08"
    assert out["paper"] == "Le Temps" and out["date"] == "1936-08-08"
    assert out["model"] == "fake"
    assert out["meta"]["n_articles"] == len(out["articles"]) == 1
    a = out["articles"][0]
    assert a["headline"] == "GREVE DES MINEURS" and a["block_ids"] == [1]
    assert a["themes"] == ["politique étrangère"]        # ce que renvoie _gen_ok


def test_summarize_page_global_themes_subset_of_taxonomy():
    out = summarize_page(_page(), _gen_ok, model_name="fake")
    assert set(out["global"]["themes"]) <= set(TAXONOMY)


def test_summarize_page_is_json_serializable():
    json.dumps(summarize_page(_page(), _gen_ok), ensure_ascii=False)


def test_summarize_page_skips_short_bodyless_fragments():
    data = _page()
    data["blocks"].append({"id": 2, "class": "bloc de texte", "conf": .9,
                           "box": [800, 900, 950, 930], "text": "trop court"})
    out = summarize_page(data, _gen_ok, model_name="fake")
    assert all("trop court" not in (a["summary"] or "") for a in out["articles"])


def test_summarize_page_never_calls_model_on_tiny_article():
    """Un article trop court est résumé par extraction, sans appeler le LLM."""
    calls = []

    def gen(p):
        calls.append(p)
        return _gen_ok(p)

    data = _page()
    data["blocks"][1]["text"] = "Court."          # < MIN_BODY_CHARS
    summarize_page(data, gen, model_name="fake")
    assert len(calls) == 1                        # uniquement la réduction globale
