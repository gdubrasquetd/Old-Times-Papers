"""Tests de la résolution (titre, date) -> ARK de fascicule : validation des entrées,
cache, ban, et extraction de l'ARK depuis la redirection Gallica (subprocess mocké)."""
from __future__ import annotations
import time
import pytest
import gallica_server as gs


class FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


# ─────────────────────────── _resolve : validation ──────────────────────

def test_resolve_missing_params(handler):
    assert handler._resolve("", "1936-05-25")["error"] == "missing parameters"
    assert handler._resolve("cb34355551z", "")["error"] == "missing parameters"


def test_resolve_invalid_ark_format(handler):
    assert handler._resolve("xyz123", "1936-05-25")["error"] == "invalid ark format"


def test_resolve_invalid_date_format(handler):
    assert handler._resolve("cb34355551z", "1936-5-25")["error"] == "invalid date format"
    assert handler._resolve("cb34355551z", "pas-une-date")["error"] == "invalid date format"


# ─────────────────────────── _resolve : cache / ban ─────────────────────

def test_resolve_returns_cached_without_network(handler):
    gs.DATE_CACHE[("cb34355551z", "1936-05-25")] = "bpt6kCACHED"
    handler._resolve_by_redirect = lambda *a: (_ for _ in ()).throw(AssertionError("réseau!"))
    assert handler._resolve("cb34355551z", "1936-05-25") == {"issue_ark": "bpt6kCACHED"}


def test_resolve_blocked_when_banned(handler):
    gs._ban_until[0] = time.time() + 1000        # ban actif
    r = handler._resolve("cb34355551z", "1936-05-25")
    assert r["error"] == "ip_banned" and r["retry_in"] > 0


# ─────────────────────────── _resolve : succès ──────────────────────────

def test_resolve_success_caches_ark(handler):
    handler._resolve_by_redirect = lambda ca, d: "bpt6k461986"
    r = handler._resolve("cb34355551z", "1936-05-25")
    assert r == {"issue_ark": "bpt6k461986"}
    assert gs.DATE_CACHE[("cb34355551z", "1936-05-25")] == "bpt6k461986"


def test_resolve_no_issue_on_date(handler):
    handler._resolve_by_redirect = lambda ca, d: None
    r = handler._resolve("cb34355551z", "1936-05-25")
    assert r == {"issue_ark": None, "reason": "no_issue_on_date"}
    assert gs.DATE_CACHE[("cb34355551z", "1936-05-25")] is None   # l'absence est cachée aussi


# ─────────────────────── _resolve_by_redirect (subprocess mocké) ────────

def test_redirect_extracts_ark_from_final_url(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    final = "https://gallica.bnf.fr/ark:/12148/bpt6k461986/date"
    monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: FakeProc(0, final))
    assert handler._resolve_by_redirect("cb34355551z", "1936-05-25") == "bpt6k461986"


def test_redirect_no_ark_returns_none(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    final = "https://gallica.bnf.fr/ark:/12148/cb34355551z/date19360525"   # page calendrier, pas de fascicule
    monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: FakeProc(0, final))
    assert handler._resolve_by_redirect("cb34355551z", "1936-05-25") is None


def test_redirect_curl_exit35_triggers_ban(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: FakeProc(35, ""))
    with pytest.raises(RuntimeError):
        handler._resolve_by_redirect("cb34355551z", "1936-05-25")
    assert gs._is_banned() is True                # exit=35 = ban IP confirmé


def test_redirect_other_error_raises_without_ban(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    monkeypatch.setattr(gs.subprocess, "run", lambda *a, **k: FakeProc(6, ""))
    with pytest.raises(RuntimeError):
        handler._resolve_by_redirect("cb34355551z", "1936-05-25")
    assert gs._is_banned() is False


# ─────────────────────────── find_free_port ─────────────────────────────

def test_find_free_port_returns_bindable_port():
    import socket
    p = gs.find_free_port(start=8765, attempts=20)
    assert p is not None and 8765 <= p < 8785
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # doit être bindable
    s.bind(("127.0.0.1", p)); s.close()
