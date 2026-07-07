"""Tests des regex d'ARK de fascicule et des garde-fous de débit (circuit breaker
de ban + token bucket). Purs / déterministes ; le temps est contrôlé par monkeypatch."""
from __future__ import annotations
import pytest
import gallica_server as gs


# ─────────────────────────── regex ARK fascicule ────────────────────────

@pytest.mark.parametrize("ark", ["bpt6k1234567", "bd6t53p0", "btv1b8449691v"])
def test_issue_ark_full_matches_known_prefixes(ark):
    assert gs.ISSUE_ARK_FULL_RE.fullmatch(ark)


@pytest.mark.parametrize("ark", ["cb34355551z", "ark12148", "bpt", "12148bpt6k"])
def test_issue_ark_full_rejects_non_issue(ark):
    assert gs.ISSUE_ARK_FULL_RE.fullmatch(ark) is None


def test_issue_ark_re_finds_ark_inside_url():
    url = "https://gallica.bnf.fr/ark:/12148/bpt6k9876543/f1.item"
    m = gs.ISSUE_ARK_RE.search(url)
    assert m and m.group(0) == "bpt6k9876543"


# ─────────────────────────── circuit breaker (ban) ──────────────────────

def test_not_banned_by_default():
    assert gs._is_banned() is False


def test_trigger_ban_sets_window(monkeypatch):
    monkeypatch.setattr(gs.time, "time", lambda: 1000.0)
    gs._trigger_ban()
    assert gs._ban_until[0] == pytest.approx(1000.0 + gs.BAN_DURATION)
    assert gs._is_banned() is True


def test_ban_expires_after_duration(monkeypatch):
    # ban déclenché à t=1000, on vérifie à t=1000+BAN_DURATION+1 -> levé
    monkeypatch.setattr(gs.time, "time", lambda: 1000.0)
    gs._trigger_ban()
    monkeypatch.setattr(gs.time, "time", lambda: 1000.0 + gs.BAN_DURATION + 1)
    assert gs._is_banned() is False


# ─────────────────────────── token bucket ───────────────────────────────

def test_acquire_token_consumes_one_when_available(monkeypatch):
    monkeypatch.setattr(gs.time, "time", lambda: 5000.0)
    gs._tb_tokens[0] = 3.0
    gs._tb_last_refill[0] = 5000.0
    gs._acquire_token()
    assert gs._tb_tokens[0] == pytest.approx(2.0)   # un token consommé, pas d'attente


def test_acquire_token_refills_over_time(monkeypatch):
    # bucket vide à t=0 ; à t=8s, refill = 8 * (1/4) = 2 tokens -> 1 consommé, reste ~1
    gs._tb_tokens[0] = 0.0
    gs._tb_last_refill[0] = 0.0
    monkeypatch.setattr(gs.time, "time", lambda: 8.0)
    gs._acquire_token()
    assert gs._tb_tokens[0] == pytest.approx(1.0)


def test_acquire_token_caps_at_capacity(monkeypatch):
    # long délai -> le refill est plafonné à TB_CAPACITY (pas d'accumulation infinie)
    gs._tb_tokens[0] = 0.0
    gs._tb_last_refill[0] = 0.0
    monkeypatch.setattr(gs.time, "time", lambda: 10_000.0)
    gs._acquire_token()
    assert gs._tb_tokens[0] == pytest.approx(gs.TB_CAPACITY - 1.0)
