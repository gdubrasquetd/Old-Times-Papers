"""Infra de test du serveur Gallica. Import hermétique : gallica_server n'ouvre
aucune socket au chargement (main() est sous garde __name__), et l'import du module
OCR échoue proprement vers des stubs. On réinitialise l'état global partagé (caches,
ban, token bucket) avant chaque test pour l'isolation."""
import sys
import pathlib

_APP = pathlib.Path(__file__).resolve().parents[1]
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import pytest
import gallica_server as gs


@pytest.fixture(autouse=True)
def reset_state():
    """Remet à zéro l'état mutable partagé du module avant chaque test."""
    gs.DATE_CACHE.clear()
    gs.YEAR_CACHE.clear()
    gs._ban_until[0] = 0.0
    gs._tb_tokens[0] = gs.TB_CAPACITY          # bucket plein -> _acquire_token ne bloque pas
    gs._tb_last_refill[0] = 0.0
    gs._last_request_time[0] = 0.0
    gs._last_content_time[0] = 0.0
    yield
    gs.DATE_CACHE.clear()
    gs.YEAR_CACHE.clear()
    gs._ban_until[0] = 0.0


@pytest.fixture
def handler():
    """Instance de Handler SANS __init__ (pas de socket) : on n'appelle que des
    méthodes de logique pure/parsing, jamais le cycle de vie HTTP réel."""
    return gs.Handler.__new__(gs.Handler)
