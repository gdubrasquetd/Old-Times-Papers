"""Tests de l'endpoint résumé : le serveur ne fait que servir un JSON précalculé.
Cache redirigé vers tmp_path — aucun accès disque réel, aucun réseau."""
from __future__ import annotations
import json
import pytest
import gallica_server as gs

ARK = "bpt6k262931k"
PAYLOAD = {
    "slug": "le_temps_1936-08-08", "paper": "Le Temps", "date": "1936-08-08",
    "issue_ark": ARK,
    "global": {"summary": "La une traite de la guerre d'Espagne.",
               "themes": ["guerre"], "keywords": ["madrid"]},
    "articles": [{"id": 0, "headline": "LA GUERRE CIVILE EN ESPAGNE",
                  "summary": "Les insurgés préparent une offensive.",
                  "themes": ["guerre"], "keywords": ["espagne"], "degraded": False}],
}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "SUMMARY_CACHE_DIR", tmp_path)
    return tmp_path


def _write(cache, ark=ARK, payload=None):
    (cache / f"{ark}.json").write_text(
        json.dumps(payload or PAYLOAD, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────── _get_summary ───────────────────────────────

def test_summary_returns_cached_payload(handler, cache):
    _write(cache)
    r = handler._get_summary(ARK)
    assert r["available"] is True
    assert r["global"]["themes"] == ["guerre"]
    assert r["articles"][0]["headline"] == "LA GUERRE CIVILE EN ESPAGNE"


def test_summary_not_generated_yet(handler, cache):
    r = handler._get_summary("bpt6kABSENT")
    assert r == {"available": False, "reason": "summary not generated"}


def test_summary_invalid_ark_format(handler, cache):
    assert handler._get_summary("cb34355551z")["error"] == "invalid ark format"
    assert handler._get_summary("")["error"] == "invalid ark format"


def test_summary_corrupted_cache_reports_error(handler, cache):
    (cache / f"{ARK}.json").write_text("{ pas du json", encoding="utf-8")
    r = handler._get_summary(ARK)
    assert "cache illisible" in r["error"]


def test_summary_never_touches_network(handler, cache, monkeypatch):
    """Aucune génération à la demande : la pipeline tourne hors-ligne."""
    def boom(*a, **k):
        raise AssertionError("le résumé ne doit jamais appeler Gallica")
    monkeypatch.setattr(gs.subprocess, "run", boom)
    _write(cache)
    assert handler._get_summary(ARK)["available"] is True


def test_summary_is_json_serializable(handler, cache):
    _write(cache)
    json.dumps(handler._get_summary(ARK), ensure_ascii=False)
