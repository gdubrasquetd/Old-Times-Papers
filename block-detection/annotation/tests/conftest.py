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


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client Flask de test. La fixture autouse `isolated_db` a deja patche
    db.DB_PATH, donc l'import de server voit la DB temporaire."""
    import server
    # Neutraliser le replenisseur (sinon thread + appels HTTP Gallica reels).
    monkeypatch.setattr(server, "_maybe_trigger_replenish", lambda: None)
    # Isoler le cache de thumbs dans tmp_path.
    monkeypatch.setattr(server, "THUMB_DIR", tmp_path / "thumbs")
    server.THUMB_DIR.mkdir(parents=True, exist_ok=True)
    server.app.config["TESTING"] = True
    with server.app.test_client() as c:
        yield c


@pytest.fixture
def make_image():
    """Factory : cree une entree image en DB (chemin bidon). Renvoie l'id."""
    def _make(slug="le_figaro_1930-05-25", journal="Le Figaro",
              date="1930-05-25", path=None):
        return db.add_image(slug, journal, date, "cb1", None,
                            path or f"/tmp/{slug}.jpg", 100, 200)
    return _make


@pytest.fixture
def make_real_image(tmp_path):
    """Factory : cree une vraie image JPEG sur disque + entree DB. Renvoie l'id."""
    from PIL import Image

    def _make(slug="le_figaro_1930-05-25", w=1000, h=1400):
        p = tmp_path / f"{slug}.jpg"
        Image.new("RGB", (w, h), (200, 200, 200)).save(p, "JPEG")
        return db.add_image(slug, "Le Figaro", "1930-05-25", "cb1", None,
                            str(p), w, h)
    return _make
