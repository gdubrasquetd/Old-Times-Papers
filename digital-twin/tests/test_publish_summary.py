"""Tests de la publication d'un summary.json vers le cache de l'app (indexé par ARK).
Base SQLite temporaire, aucun accès à la vraie base d'annotation."""
from __future__ import annotations
import json
import sqlite3
import pytest

from publish_summary import publish, slug_to_ark

SLUG = "le_temps_1936-08-08"
ARK = "bpt6k262931k"


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "annotations.db"
    con = sqlite3.connect(p)
    con.execute("create table images (slug text, issue_ark text)")
    con.execute("insert into images values (?, ?)", (SLUG, ARK))
    con.execute("insert into images values (?, ?)", ("sans_ark_1900-01-01", ""))
    con.commit(); con.close()
    return p


def _write_summary(tmp_path, **extra):
    data = {"slug": SLUG, "paper": "Le Temps", "global": {"summary": "s"}, "articles": []}
    data.update(extra)
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ─────────────────────────── slug_to_ark ────────────────────────────────

def test_slug_to_ark_found(db):
    assert slug_to_ark(SLUG, db) == ARK


def test_slug_to_ark_unknown_slug(db):
    assert slug_to_ark("inconnu_1900-01-01", db) is None


def test_slug_to_ark_empty_ark_is_none(db):
    assert slug_to_ark("sans_ark_1900-01-01", db) is None


def test_slug_to_ark_missing_db(tmp_path):
    assert slug_to_ark(SLUG, tmp_path / "absente.db") is None


# ─────────────────────────── publish ────────────────────────────────────

def test_publish_writes_file_named_by_ark(tmp_path, db):
    src = _write_summary(tmp_path)
    cache = tmp_path / "cache"
    out = publish(src, cache_dir=cache, db_path=db)
    assert out == cache / f"{ARK}.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["issue_ark"] == ARK and data["slug"] == SLUG


def test_publish_creates_cache_dir(tmp_path, db):
    cache = tmp_path / "nested" / "cache"
    publish(_write_summary(tmp_path), cache_dir=cache, db_path=db)
    assert cache.is_dir()


def test_publish_prefers_ark_already_in_summary(tmp_path):
    # pas de base : l'ARK embarqué dans le JSON doit suffire
    src = _write_summary(tmp_path, issue_ark="bpt6kDEJALA")
    cache = tmp_path / "cache"
    out = publish(src, cache_dir=cache, db_path=tmp_path / "absente.db")
    assert out.name == "bpt6kDEJALA.json"


def test_publish_returns_none_and_writes_nothing_without_ark(tmp_path):
    src = _write_summary(tmp_path)
    cache = tmp_path / "cache"
    assert publish(src, cache_dir=cache, db_path=tmp_path / "absente.db") is None
    assert not cache.exists()          # mieux vaut rien écrire qu'un mauvais nom
