"""Tests d'intégration du LLM local : nécessitent le GGUF, llama-cpp-python et le GPU.
Exclus du run par défaut (`addopts = -m "not integration"` dans pytest.ini).

    pytest -m integration digital-twin
"""
from __future__ import annotations
import json
import pytest

from summarize import (MODEL_DIR, MODEL_PRIMARY, TAXONOMY, _load_llm,
                       make_generator, parse_llm_json, summarize_article)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def llm():
    if not (MODEL_DIR / MODEL_PRIMARY).exists():
        pytest.skip(f"modèle absent : {MODEL_DIR / MODEL_PRIMARY}")
    obj, name = _load_llm()
    return obj, name


def test_model_loads_within_vram(llm):
    obj, name = llm
    assert "qwen" in name


def test_generator_emits_valid_json(llm):
    obj, _ = llm
    raw = make_generator(obj)("Résume : le ministre a démissionné ce matin après un vote.")
    parsed = parse_llm_json(raw)
    assert parsed is not None and "summary" in parsed


def test_summarize_real_article_is_grounded(llm):
    obj, _ = llm
    body = ("Le président du conseil a reçu ce matin les délégués du syndicat des mineurs "
            "afin d'examiner les revendications salariales. Une grève est envisagée si "
            "aucun accord n'est trouvé avant la fin de la semaine.")
    out = summarize_article(make_generator(obj), "LA GRÈVE DES MINEURS", body)
    assert out["degraded"] is False
    assert len(out["summary"]) > 20
    assert set(out["themes"]) <= set(TAXONOMY)
    assert len(out["themes"]) <= 3
    json.dumps(out, ensure_ascii=False)
