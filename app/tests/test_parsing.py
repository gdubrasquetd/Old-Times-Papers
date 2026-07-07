"""Tests du parsing XML : API Issues (année -> {date: ark}) et Dublin Core (métadonnées).
Le réseau est remplacé par un stub de _fetch_with_curl (la frontière I/O)."""
from __future__ import annotations
import pytest
import gallica_server as gs

ISSUES_XML = """<?xml version="1.0"?>
<root>
  <issue ark="bpt6k461986" dayOfYear="146">1936/05/25 (Numéro 1)</issue>
  <issue ark="bd6t53p9x" dayOfYear="147">1936/05/26</issue>
  <issue ark="btv1b8449q" dayOfYear="148">1936/05/27</issue>
</root>"""

DC_XML = """<oai_dc:dc xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>Le Figaro</dc:title>
  <dc:date>1936-05-25</dc:date>
  <dc:publisher>Figaro (Paris)</dc:publisher>
  <dc:description>Grand quotidien.</dc:description>
  <dc:description>Domaine public.</dc:description>
  <dc:identifier>ark:/12148/bpt6k461986</dc:identifier>
</oai_dc:dc>"""


# ─────────────────────────── _get_year_issues (parse) ───────────────────

def test_get_year_issues_parses_all_prefixes(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    handler._fetch_with_curl = lambda url: (url, ISSUES_XML)
    out = handler._get_year_issues("cb34355551z", 1936)
    assert out == {"1936-05-25": "bpt6k461986",
                   "1936-05-26": "bd6t53p9x",
                   "1936-05-27": "btv1b8449q"}


def test_get_year_issues_builds_correct_url(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    seen = {}
    def _fetch(url):
        seen["url"] = url
        return url, ISSUES_XML
    handler._fetch_with_curl = _fetch
    handler._get_year_issues("cb34355551z", 1936)
    assert seen["url"] == ("https://gallica.bnf.fr/services/Issues"
                           "?ark=ark:/12148/cb34355551z/date&date=1936")


def test_get_year_issues_empty_body_returns_empty(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    handler._fetch_with_curl = lambda url: (url, "<root></root>")
    assert handler._get_year_issues("cb34355551z", 1900) == {}


def test_get_year_issues_uses_cache(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    gs.YEAR_CACHE[("cb34355551z", 1936)] = {"1936-05-25": "bpt6kCACHED"}
    def _boom(url):
        raise AssertionError("ne doit pas toucher le réseau quand c'est en cache")
    handler._fetch_with_curl = _boom
    out = handler._get_year_issues("cb34355551z", 1936)
    assert out == {"1936-05-25": "bpt6kCACHED"}


def test_get_year_issues_caches_result(handler, monkeypatch):
    monkeypatch.setattr(gs, "HAS_CURL", True)
    handler._fetch_with_curl = lambda url: (url, ISSUES_XML)
    handler._get_year_issues("cb34355551z", 1936)
    assert ("cb34355551z", 1936) in gs.YEAR_CACHE


# ─────────────────────────── _parse_dc ──────────────────────────────────

def test_parse_dc_extracts_fields(handler):
    d = handler._parse_dc(DC_XML)
    assert d["title"] == "Le Figaro"
    assert d["date"] == "1936-05-25"
    assert d["publisher"] == "Figaro (Paris)"
    assert d["identifier"] == "ark:/12148/bpt6k461986"


def test_parse_dc_joins_multiple_descriptions(handler):
    d = handler._parse_dc(DC_XML)
    assert d["description"] == "Grand quotidien. Domaine public."


def test_parse_dc_missing_fields_are_empty(handler):
    d = handler._parse_dc("<oai_dc:dc></oai_dc:dc>")
    assert d == {"title": "", "date": "", "publisher": "",
                 "description": "", "identifier": ""}
