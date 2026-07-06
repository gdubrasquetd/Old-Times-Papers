"""
Fixtures partagees pour les tests de l'outil d'annotation.

Strategie : chaque test recoit une DB SQLite vierge dans un repertoire
temporaire (tmp_path). On reroute db.DB_PATH via monkeypatch et on appelle
init_db() pour seeder le schema + les labels par defaut. La DB de production
data/annotations.db n'est jamais touchee par les tests.
"""
from __future__ import annotations
import sys, pathlib

import pytest

# Permet `import db`, `import server` directement depuis les tests.
_ANNOT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ANNOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_ANNOT_ROOT))

import db  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirige db.DB_PATH vers une DB fraiche pour chaque test."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "annotations.db")
    db.init_db()
    yield
