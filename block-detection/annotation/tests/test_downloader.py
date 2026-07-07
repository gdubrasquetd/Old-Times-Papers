"""Tests du téléchargeur. Logique pure (iter_dates, parsing XML) + logique de
batch avec le réseau MOCKÉ → entièrement hermétique (aucun appel Gallica)."""
from __future__ import annotations
import downloader
import db


# ─────────────────────────── iter_dates (pur) ───────────────────────────

def test_iter_dates_full_year():
    d = downloader.iter_dates(1930)
    assert len(d) == 365
    assert d[0] == "1930-01-01" and d[-1] == "1930-12-31"


def test_iter_dates_leap_year_has_366():
    assert len(downloader.iter_dates(1936)) == 366        # 1936 est bissextile


def test_iter_dates_february_non_leap():
    d = downloader.iter_dates(1930, 2)
    assert len(d) == 28
    assert d[0] == "1930-02-01" and d[-1] == "1930-02-28"


def test_iter_dates_february_leap():
    assert len(downloader.iter_dates(1936, 2)) == 29


def test_iter_dates_december_boundary():
    d = downloader.iter_dates(1930, 12)
    assert len(d) == 31
    assert d[-1] == "1930-12-31"


# ─────────────────── fetch_year_issues (parsing du XML) ──────────────────

def test_fetch_year_issues_parses_ark_and_dayofyear(monkeypatch):
    sample = (
        '<results>'
        '<issue ark="bpt6k123abc" dayOfYear="146">x</issue>'
        '<issue ark="bpt6k999xyz" dayOfYear="1">y</issue>'
        '</results>'
    )
    monkeypatch.setattr(downloader, "fetch_text", lambda url, timeout=30: sample)
    assert downloader.fetch_year_issues("cbF", 1936) == {146: "bpt6k123abc", 1: "bpt6k999xyz"}


def test_fetch_year_issues_empty_when_no_match(monkeypatch):
    monkeypatch.setattr(downloader, "fetch_text", lambda url, timeout=30: "<results></results>")
    assert downloader.fetch_year_issues("cbF", 1936) == {}


def test_fetch_year_issues_builds_correct_url(monkeypatch):
    seen = {}
    def fake(url, timeout=30):
        seen["url"] = url
        return ""
    monkeypatch.setattr(downloader, "fetch_text", fake)
    downloader.fetch_year_issues("cb34355551z", 1936)
    assert "ark:/12148/cb34355551z/date" in seen["url"] and "date=1936" in seen["url"]


# ─────────────────── download_batch (réseau mocké) ───────────────────────

def _one_issue_per_year(ark, year):
    return {1: "bpt6k" + ark}          # un seul jour téléchargeable par (journal, année)


def test_download_batch_stops_at_n(monkeypatch):
    monkeypatch.setattr(downloader, "JOURNAUX", [("figaro", "Le Figaro", "cbF", 1900, 1939)])
    monkeypatch.setattr(downloader, "fetch_year_issues",
                        lambda ark, year: {1: "a", 2: "b", 3: "c"})
    calls = []
    monkeypatch.setattr(downloader, "download_one",
                        lambda slug, *a: (calls.append(a[2]) or True))   # a[2] = iso_date
    n = downloader.download_batch(n=2, year_range=(1930, 1930), delay=0,
                                  shuffle=False, balance=False)
    assert n == 2 and len(calls) == 2


def test_download_batch_skips_already_in_db(monkeypatch):
    monkeypatch.setattr(downloader, "JOURNAUX", [("figaro", "Le Figaro", "cbF", 1900, 1939)])
    monkeypatch.setattr(downloader, "fetch_year_issues", _one_issue_per_year)

    def _must_not_download(*a):
        raise AssertionError("ne doit pas télécharger un slug déjà présent")
    monkeypatch.setattr(downloader, "download_one", _must_not_download)
    # La seule une candidate est déjà en DB → rien à faire.
    db.add_image("figaro_1930-01-01", "Le Figaro", "1930-01-01", "cbF", None, "/tmp/x.jpg", 1, 1)
    n = downloader.download_batch(n=5, year_range=(1930, 1930), delay=0, shuffle=False)
    assert n == 0


def test_download_batch_balances_toward_underrepresented(monkeypatch):
    monkeypatch.setattr(downloader, "JOURNAUX", [
        ("figaro", "Le Figaro", "cbF", 1900, 1939),
        ("temps",  "Le Temps",  "cbT", 1900, 1939),
    ])
    monkeypatch.setattr(downloader, "fetch_year_issues", _one_issue_per_year)
    order = []
    monkeypatch.setattr(downloader, "download_one", lambda slug, *a: (order.append(slug) or True))
    # Le Figaro est déjà représenté (1 une 'done') → le batch doit servir Le Temps d'abord.
    fid = db.add_image("figaro_1929-01-01", "Le Figaro", "1929-01-01", "cbF", None, "/tmp/x.jpg", 1, 1)
    db.set_image_status(fid, "done")
    downloader.download_batch(n=1, year_range=(1930, 1930), delay=0, shuffle=False, balance=True)
    assert order == ["temps"]
