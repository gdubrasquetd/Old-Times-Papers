#!/usr/bin/env python3
"""
gallica_unes_server.py — Mini serveur local pour naviguer dans les unes
des journaux français numérisés par Gallica (BnF).

POURQUOI UN SERVEUR ?
    L'API SRU de Gallica ne supporte pas CORS, donc un simple fichier HTML
    ouvert dans le navigateur ne peut pas l'interroger directement.
    Ce script lance un serveur local qui sert la page HTML et proxifie
    les requêtes vers Gallica côté Python (où CORS ne s'applique pas).

USAGE :
    python gallica_unes_server.py
    # ouvre automatiquement http://localhost:8765 dans le navigateur

DÉPENDANCES :
    aucune (Python standard library uniquement)
"""
import http.server
import socketserver
import urllib.parse
import json
import webbrowser
import threading
import re
import socket
import sys
import time
import subprocess
import shutil
import platform
import pathlib

DEFAULT_PORT = 8765
# UA d'un vrai Chrome récent
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")

# Préfixes ARK utilisés par Gallica pour les numéros de presse :
#   - bpt6k  : le plus courant (texte plein, ancien)
#   - bd6t   : numérisations plus récentes (ex. Figaro années 1940)
#   - btv1b  : parfois utilisé aussi (manuscrits/iconographie, mais aussi presse)
ISSUE_ARK_PATTERN = r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+"
ISSUE_ARK_RE = re.compile(ISSUE_ARK_PATTERN, re.IGNORECASE)
ISSUE_ARK_FULL_RE = re.compile(rf"^{ISSUE_ARK_PATTERN}$", re.IGNORECASE)
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv

# Détection du backend HTTP
CURL_PATH = shutil.which("curl") or shutil.which("curl.exe")
HAS_CURL = CURL_PATH is not None

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from OCR.ocr_local import (run_ocr, run_ocr_blocks, run_layout_blocks,
                                run_ocr_region_kraken, run_ocr_full_kraken,
                                download_image, HAS_TESSERACT as TESSERACT_OK,
                                HAS_SURYA as SURYA_OK,
                                HAS_PADDLE as PADDLE_OK,
                                HAS_KRAKEN as KRAKEN_OK)
except ImportError:
    TESSERACT_OK = SURYA_OK = PADDLE_OK = KRAKEN_OK = False
    def run_ocr(issue_ark, cache_dir, img_cache_dir=None):
        return {"error": "Module OCR introuvable (OCR/ocr_local.py)"}
    def run_ocr_blocks(issue_ark, cache_dir, img_cache_dir=None):
        return {"error": "Module OCR introuvable (OCR/ocr_local.py)"}
    def run_layout_blocks(issue_ark, cache_dir, img_cache_dir=None):
        return {"error": "Module OCR introuvable (OCR/ocr_local.py)"}
    def run_ocr_region_kraken(issue_ark, x0, y0, x1, y1, img_cache_dir):
        return {"error": "Module OCR introuvable (OCR/ocr_local.py)"}
    def run_ocr_full_kraken(issue_ark, cache_dir, img_cache_dir=None):
        return {"error": "Module OCR introuvable (OCR/ocr_local.py)"}
    def download_image(issue_ark, img_cache_dir):
        return {"error": "Module OCR introuvable (OCR/ocr_local.py)"}

# CONCURRENCE : 1 = strictement séquentiel vers Gallica (mode conservateur,
# choisi suite aux bannissements IP répétés). La requête suivante part dès
# que la précédente a reçu sa réponse — pas de délai artificiel ajouté.
GALLICA_SEMAPHORE = threading.Semaphore(1)

# Délai minimum entre 2 requêtes — quasi nul (juste anti-race). Le rythme réel
# est dicté par le temps de réponse de Gallica (~0.5–1s) + le token bucket.
MIN_DELAY_BETWEEN_REQUESTS = 0.05  # secondes
_last_request_time = [0.0]
_request_lock = threading.Lock()

# Token bucket — filet de sécurité contre les bursts soutenus.
# Capacité : 20 tokens → une page entière (17 quotidiens) passe d'un coup
#   à la vitesse naturelle de Gallica, sans throttle artificiel.
# Refill : 1 token / 4s → ~15 req/min en régime soutenu (= confortable même
#   si l'utilisateur enchaîne plusieurs dates différentes).
_tb_lock = threading.Lock()
_tb_tokens = [20.0]
_tb_last_refill = [time.time()]
TB_CAPACITY  = 20.0
TB_REFILL_RATE = 1.0 / 4.0  # tokens/seconde → ~15 req/min max soutenu

def _acquire_token():
    """Attend qu'un token soit disponible avant d'envoyer une requête Gallica."""
    while True:
        with _tb_lock:
            now = time.time()
            elapsed = now - _tb_last_refill[0]
            _tb_last_refill[0] = now
            _tb_tokens[0] = min(TB_CAPACITY, _tb_tokens[0] + elapsed * TB_REFILL_RATE)
            if _tb_tokens[0] >= 1.0:
                _tb_tokens[0] -= 1.0
                return
            wait = (1.0 - _tb_tokens[0]) / TB_REFILL_RATE
        time.sleep(min(wait, 1.0))  # re-vérifier toutes les 1s max

# Circuit breaker — détection de bannissement IP temporaire.
# Quand Gallica coupe le SSL (exit=35) après toutes les tentatives, on active
# le ban localement : toutes les requêtes sont bloquées pendant BAN_DURATION
# secondes sans toucher à Gallica, puis la limite se lève automatiquement.
BAN_DURATION = 1800  # 30 minutes (Gallica peut blacklister 15-60 min)
_ban_lock = threading.Lock()
_ban_until = [0.0]  # epoch timestamp; 0.0 = pas de ban actif

def _is_banned() -> bool:
    return time.time() < _ban_until[0]

def _trigger_ban():
    with _ban_lock:
        _ban_until[0] = time.time() + BAN_DURATION
    if VERBOSE:
        resumes = time.strftime("%H:%M:%S", time.localtime(_ban_until[0]))
        print(f"  [BAN] IP bloquée par Gallica — reprise automatique à {resumes} "
              f"(dans {BAN_DURATION//60} min)")

# Cache mémoire par date exacte : {(catalog_ark, iso_date): bpt6k_ark | None}
DATE_CACHE = {}
DATE_CACHE_LOCK = threading.Lock()

# Conservé uniquement pour /debug-raw et /api/cache
YEAR_CACHE = {}
CACHE_LOCK = threading.Lock()

# Cache disque pour OCR et métadonnées DC — persiste entre les sessions.
CACHE_DIR      = pathlib.Path(__file__).parent / "cache"
OCR_CACHE_DIR  = CACHE_DIR / "ocr"
IMG_CACHE_DIR  = CACHE_DIR / "ocr_img"
META_CACHE_DIR = CACHE_DIR / "meta"

# Semaphore séparé pour les requêtes de contenu (OCR, DC) — plus restrictif
# que pour les métadonnées légères (API Issues).
OCR_SEMAPHORE = threading.Semaphore(1)
MIN_DELAY_CONTENT = 2.0  # secondes entre requêtes de contenu
_last_content_time = [0.0]
_content_lock = threading.Lock()

# =============================================================================
# PAGE HTML (servie à la racine)
# =============================================================================
HTML_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Unes des journaux français — Gallica BnF</title>
<style>
  :root {
    --bg:#f4f0e8; --paper:#fff; --ink:#1a1a1a; --muted:#666;
    --rule:#444; --link:#1a4a7a; --accent:#8b2c2c;
    --ok:#2d6a4f; --warn:#b87333;
  }
  * { box-sizing: border-box; }
  body { font-family: Georgia, "Times New Roman", serif; background: var(--bg);
         margin:0; padding:1.5rem; color: var(--ink); }
  header { border-bottom: 3px double var(--rule); padding-bottom: 1rem; margin-bottom: 1.5rem; }
  h1 { margin: 0 0 .5rem 0; font-size: 1.8rem; }
  .lede { color: var(--muted); font-style: italic; max-width: 720px; margin-bottom: 1rem; }
  .controls { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
              background: var(--paper); padding: 1rem; box-shadow: 0 2px 6px rgba(0,0,0,.08); }
  .controls label { font-weight: bold; }
  .controls input[type="date"] { font-family: inherit; font-size: 1.1rem;
                                 padding: .4rem .6rem; border: 1px solid #aaa; background: white; }
  .controls .nav-btn { font-family: inherit; padding: .4rem .8rem; cursor: pointer;
                       background: var(--ink); color: white; border: none; font-size: .9rem; }
  .controls .nav-btn:hover { background: var(--accent); }
  .controls .nav-btn.secondary { background: var(--muted); }
  .controls .separator { color: #bbb; margin: 0 .2rem; user-select: none; }

  /* Timeline horizontale */
  .timeline-wrap { background: var(--paper); padding: .8rem 1rem; margin-top: .6rem;
                   box-shadow: 0 2px 6px rgba(0,0,0,.08); }
  .timeline-label { font-size: .85rem; color: var(--muted); margin-bottom: .4rem; }
  .timeline { display: flex; overflow-x: auto; gap: 0; padding: .3rem 0 .8rem 0;
              scrollbar-width: thin; scrollbar-color: #aaa #eee;
              border-bottom: 1px solid #ddd; position: relative; }
  .timeline::-webkit-scrollbar { height: 8px; }
  .timeline::-webkit-scrollbar-track { background: #eee; }
  .timeline::-webkit-scrollbar-thumb { background: #aaa; border-radius: 4px; }
  .year-tick { flex: 0 0 auto; padding: .4rem .5rem; margin: 0; cursor: pointer;
               background: transparent; border: none; border-right: 1px solid #eee;
               font-family: inherit; font-size: .85rem; color: #444;
               position: relative; min-width: 56px; text-align: center;
               transition: background-color .1s; }
  .year-tick:hover { background: #f0e9d8; color: var(--ink); }
  .year-tick.decade { font-weight: bold; background: #faf6ed; }
  .year-tick.active { background: var(--accent); color: white; font-weight: bold; }
  .year-tick.active:hover { background: var(--accent); }
  .year-tick .weekday { display: block; font-size: .65rem; color: #888;
                        margin-top: 2px; font-style: italic; }
  .year-tick.active .weekday { color: rgba(255,255,255,.85); }
  .year-tick.cached::after { content: ""; position: absolute; bottom: 2px; left: 50%;
                             transform: translateX(-50%); width: 4px; height: 4px;
                             background: var(--ok); border-radius: 50%; }
  .year-tick.active.cached::after { background: white; }
  .date-info { color: var(--muted); font-style: italic; flex-basis: 100%; }
  .date-info strong { color: var(--ink); font-style: normal; }
  #status { padding: .5rem .8rem; margin-top: .5rem; background: #fffbea;
            border-left: 3px solid var(--warn); font-size: .85rem; }
  #status.ok    { background: #e8f5e9; border-color: var(--ok); }
  #status.error { background: #ffe8e8; border-color: var(--accent); }
  #ban-banner { display: none; background: #7a1c1c; color: white;
                padding: .6rem 1.2rem; font-size: .9rem; text-align: center;
                position: sticky; top: 0; z-index: 100; }
  #ban-banner strong { font-weight: bold; }
  h2 { font-size: 1.4rem; margin: 2rem 0 .8rem 0; border-bottom: 1px solid #ccc;
       padding-bottom: .3rem; }
  h3 { font-size: 1.05rem; margin: 1rem 0 .4rem 0; color: var(--accent); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 1.2rem; }
  .card { background: var(--paper); padding: .8rem; box-shadow: 0 2px 6px rgba(0,0,0,.1);
          display: flex; flex-direction: column; }
  .card h4 { font-size: 1rem; margin: 0 0 .3rem 0; }
  .card .thumb-wrap { position: relative; background: #f0ece4; min-height: 280px;
                      display: flex; align-items: center; justify-content: center;
                      border: 1px solid #ccc; overflow: hidden; }
  .card img { width: 100%; height: auto; display: block; }
  .card .state { color: var(--muted); font-size: .85rem; font-style: italic;
                 text-align: center; padding: 1rem; }
  .card .state.loading::after { content: " ⏳"; }
  .card .state.error { color: var(--accent); }
  .card .thumb-wrap a:hover img { opacity: .88; }
  .card .thumb-wrap a { display: block; }
  .card .meta { font-size: .8rem; color: var(--muted); margin-top: .5rem;
                display: flex; justify-content: space-between; align-items: center; gap: .5rem; }
  .card .meta a { color: var(--link); text-decoration: none; }
  .card .meta a:hover { text-decoration: underline; }
  .card .meta code { font-size: .7rem; background: #eee; padding: 1px 3px; }
  .categories { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.2rem; }
  .category { background: var(--paper); padding: .9rem 1.1rem; box-shadow: 0 2px 6px rgba(0,0,0,.08); }
  .category ul { list-style: none; padding: 0; margin: 0; }
  .category li { padding: .15rem 0; font-size: .9rem; }
  .category li a { color: var(--link); text-decoration: none; }
  .category li a:hover { text-decoration: underline; }
  .category li .years { color: var(--muted); font-size: .8rem; }
  .count-badge { display: inline-block; background: var(--ink); color: white;
                 padding: .1rem .5rem; font-size: .8rem; margin-left: .5rem;
                 vertical-align: middle; font-family: sans-serif; }
  footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #ccc;
           font-size: .85rem; color: var(--muted); }
  footer code { background: #eee; padding: 1px 4px; font-size: .8rem; }
</style>
</head>
<body>

<div id="ban-banner"></div>

<header>
  <h1>📰 Unes des journaux français — Gallica BnF</h1>
  <p class="lede">
    Choisissez un jour, puis naviguez à travers les années sur la timeline pour voir
    le même jour à différentes époques (1850 → 1955).
  </p>
  <div class="controls">
    <label for="datePicker">Date :</label>
    <button class="nav-btn" id="prevDay" title="Jour précédent (←)">← Jour</button>
    <input type="date" id="datePicker" min="1850-01-01" max="1955-12-31"/>
    <button class="nav-btn" id="nextDay" title="Jour suivant (→)">Jour →</button>
    <span class="separator">|</span>
    <button class="nav-btn secondary" id="todayBtn" title="Revenir à aujourd'hui (en 1930)">↻ Aujourd'hui</button>
    <div class="date-info" id="dateInfo"></div>
    <div id="status">Prêt.</div>
  </div>

  <div class="timeline-wrap">
    <div class="timeline-label">Même jour, autres années :</div>
    <div class="timeline" id="timeline"></div>
  </div>
</header>

<section id="mainPapers">
  <h2>Quotidiens nationaux <span class="count-badge" id="mainCount">0</span></h2>
  <div class="grid" id="mainGrid"></div>
</section>

<section id="otherPapers">
  <h2>Autres titres disponibles à cette date <span class="count-badge" id="otherCount">0</span></h2>
  <p class="lede" style="margin-top:0">
    Cliquez sur un titre pour ouvrir le numéro (ou le calendrier de l'année) sur Gallica.
  </p>
  <div class="categories" id="otherCategories"></div>
</section>

<footer>
  Source : <a href="https://gallica.bnf.fr">Gallica BnF</a> (domaine public).<br/>
  Résolution ARK via <code>/api/resolve?ark=cb…&date=YYYY-MM-DD</code> (proxy local vers SRU).
  Vignette via <code>iiif/ark:/12148/{bpt6k…}/f1/full/400,/0/native.jpg</code>.
  Lien principal direct vers le fascicule résolu.
</footer>

<script>
// =============================================================================
// CATALOGUE
// =============================================================================
const NEWSPAPERS = [
  { tier:1, title:"L'Action française",       ark:"cb326819451", start:1908, end:1944, cat:"nat" },
  { tier:1, title:"La Croix",                 ark:"cb343631418", start:1880, end:1944, cat:"nat" },
  { tier:1, title:"L'Écho de Paris",          ark:"cb34429768r", start:1884, end:1944, cat:"nat" },
  { tier:1, title:"Excelsior",                ark:"cb32771891w", start:1910, end:1940, cat:"nat" },
  { tier:1, title:"Le Figaro",                ark:"cb34355551z", start:1854, end:1955, cat:"nat" },
  { tier:1, title:"Le Gaulois",               ark:"cb32779904b", start:1868, end:1929, cat:"nat" },
  { tier:1, title:"L'Humanité",               ark:"cb327877302", start:1904, end:1944, cat:"nat" },
  { tier:1, title:"L'Intransigeant",          ark:"cb32793876w", start:1880, end:1944, cat:"nat" },
  { tier:1, title:"Le Journal",               ark:"cb34473289x", start:1892, end:1944, cat:"nat" },
  { tier:1, title:"Journal des débats",       ark:"cb39294634r", start:1814, end:1944, cat:"nat" },
  { tier:1, title:"Le Matin",                 ark:"cb328123058", start:1884, end:1944, cat:"nat" },
  { tier:1, title:"L'Œuvre",                  ark:"cb34429265b", start:1904, end:1944, cat:"nat" },
  { tier:1, title:"Paris-Soir",               ark:"cb34431897x", start:1923, end:1944, cat:"nat" },
  { tier:1, title:"Le Petit Journal",         ark:"cb32895690j", start:1863, end:1944, cat:"nat" },
  { tier:1, title:"Le Petit Parisien",        ark:"cb34419111x", start:1876, end:1944, cat:"nat" },
  { tier:1, title:"Le Populaire",             ark:"cb34393339w", start:1916, end:1944, cat:"nat" },
  { tier:1, title:"Le Temps",                 ark:"cb34431794k", start:1861, end:1942, cat:"nat" },

  { tier:2, title:"L'Aurore",                 ark:"cb327068466", start:1897, end:1916, cat:"nat2" },
  { tier:2, title:"La Justice",               ark:"cb32802914w", start:1880, end:1930, cat:"nat2" },
  { tier:2, title:"La Lanterne",              ark:"cb32805827b", start:1877, end:1928, cat:"nat2" },
  { tier:2, title:"Le Rappel",                ark:"cb327971433", start:1869, end:1928, cat:"nat2" },
  { tier:2, title:"Le Siècle",                ark:"cb343480827", start:1836, end:1932, cat:"nat2" },
  { tier:2, title:"La Presse",                ark:"cb34448033b", start:1836, end:1935, cat:"nat2" },
  { tier:2, title:"Gil Blas",                 ark:"cb327795290", start:1879, end:1938, cat:"nat2" },
  { tier:2, title:"La Libre Parole",          ark:"cb32807774s", start:1892, end:1924, cat:"nat2" },
  { tier:2, title:"La République française",  ark:"cb327990236", start:1871, end:1924, cat:"nat2" },
  { tier:2, title:"L'Univers",                ark:"cb32830744r", start:1833, end:1929, cat:"nat2" },
  { tier:2, title:"L'Éclair",                 ark:"cb32766807w", start:1888, end:1930, cat:"nat2" },
  { tier:2, title:"Le Pays",                  ark:"cb327853990", start:1849, end:1914, cat:"nat2" },
  { tier:2, title:"Le Soleil",                ark:"cb32867913h", start:1873, end:1922, cat:"nat2" },
  { tier:2, title:"Le Radical",               ark:"cb327822069", start:1881, end:1928, cat:"nat2" },
  { tier:2, title:"Le Petit Bleu",            ark:"cb32834014m", start:1899, end:1944, cat:"nat2" },
  { tier:2, title:"L'Action",                 ark:"cb32684841d", start:1903, end:1923, cat:"nat2" },
  { tier:2, title:"Le Cri du Peuple",         ark:"cb32747884n", start:1883, end:1922, cat:"nat2" },

  { tier:2, title:"L'Ouest-Éclair (Rennes)",  ark:"cb344293641", start:1899, end:1944, cat:"reg" },
  { tier:2, title:"Le Petit Marseillais",     ark:"cb32834409b", start:1868, end:1944, cat:"reg" },
  { tier:2, title:"Le Petit Provençal",       ark:"cb32834666c", start:1880, end:1944, cat:"reg" },
  { tier:2, title:"La Dépêche (Toulouse)",    ark:"cb326952494", start:1870, end:1944, cat:"reg" },
  { tier:2, title:"Le Progrès (Lyon)",        ark:"cb32852188t", start:1859, end:1944, cat:"reg" },
  { tier:2, title:"L'Express du Midi",        ark:"cb327695397", start:1891, end:1938, cat:"reg" },
  { tier:2, title:"Le Petit Méridional",      ark:"cb328346769", start:1876, end:1944, cat:"reg" },
  { tier:2, title:"Le Journal de Rouen",      ark:"cb32802139k", start:1762, end:1944, cat:"reg" },
  { tier:2, title:"Le Phare de la Loire",     ark:"cb328311053", start:1851, end:1944, cat:"reg" },
  { tier:2, title:"Le Nouvelliste de Lyon",   ark:"cb327832581", start:1879, end:1944, cat:"reg" },
  { tier:2, title:"Le Petit Dauphinois",      ark:"cb328349881", start:1878, end:1944, cat:"reg" },
  { tier:2, title:"L'Écho du Nord",           ark:"cb32766858p", start:1819, end:1944, cat:"reg" },

  { tier:2, title:"Le Charivari",             ark:"cb344683867", start:1832, end:1937, cat:"sat" },
  { tier:2, title:"L'Assiette au beurre",     ark:"cb343486026", start:1901, end:1936, cat:"sat" },
  { tier:2, title:"Le Rire",                  ark:"cb344290937", start:1894, end:1944, cat:"sat" },

  { tier:2, title:"La Fronde",                ark:"cb34423920q", start:1897, end:1905, cat:"fem" },
  { tier:2, title:"La Citoyenne",             ark:"cb32747562r", start:1881, end:1891, cat:"fem" },
  { tier:2, title:"Femina",                   ark:"cb32770329s", start:1901, end:1944, cat:"fem" },

  { tier:2, title:"Mercure de France",        ark:"cb344279340", start:1890, end:1944, cat:"lit" },
  { tier:2, title:"La Revue blanche",         ark:"cb344278530", start:1889, end:1903, cat:"lit" },
  { tier:2, title:"Comoedia",                 ark:"cb32745939p", start:1907, end:1944, cat:"lit" },

  { tier:2, title:"L'Auto",                   ark:"cb327012523", start:1900, end:1944, cat:"sport" },
  { tier:2, title:"Le Vélo",                  ark:"cb32885372s", start:1892, end:1904, cat:"sport" },

  { tier:2, title:"Journal officiel",         ark:"cb34378481r", start:1869, end:1944, cat:"off" },
];

const CATEGORIES = {
  "nat2":  "Autres quotidiens nationaux",
  "reg":   "Presse régionale",
  "sat":   "Presse satirique & illustrée",
  "fem":   "Presse féminine",
  "lit":   "Presse littéraire & culturelle",
  "sport": "Presse sportive",
  "off":   "Presse officielle",
};

const MONTHS   = ["janvier","février","mars","avril","mai","juin",
                  "juillet","août","septembre","octobre","novembre","décembre"];
const WEEKDAYS = ["dimanche","lundi","mardi","mercredi","jeudi","vendredi","samedi"];

// =============================================================================
// API LOCALE (proxy vers Gallica SRU)
// =============================================================================
async function resolveIssueArk(catalogArk, isoDate) {
  const url = `/api/resolve?ark=${encodeURIComponent(catalogArk)}&date=${encodeURIComponent(isoDate)}`;
  try {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (data.error) return { error: data.error };
    return data.issue_ark || null;
  } catch (e) {
    return { error: e.message };
  }
}

function iiifThumb(bptArk, width=400) {
  return `https://gallica.bnf.fr/iiif/ark:/12148/${bptArk}/f1/full/${width},/0/native.jpg`;
}

// Limite de concurrence — 1 = strictement séquentiel côté navigateur,
// pour aligner avec le rate-limit serveur et donner un feedback visuel net.
class Pool {
  constructor(max) { this.max=max; this.active=0; this.queue=[]; }
  async run(task) {
    while (this.active >= this.max) await new Promise(res => this.queue.push(res));
    this.active++;
    try { return await task(); }
    finally { this.active--; if (this.queue.length) this.queue.shift()(); }
  }
}
const pool = new Pool(1);

// =============================================================================
// AFFICHAGE
// =============================================================================
const $ = id => document.getElementById(id);
const fmt = d => `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
const compact = iso => iso.replace(/-/g, "");

let renderToken = 0;

function buildCardSkeleton(np, isoDate) {
  const pageUrl = `https://gallica.bnf.fr/ark:/12148/${np.ark}/date${compact(isoDate)}`;
  const card = document.createElement("article");
  card.className = "card";
  card.innerHTML = `
    <h4>${np.title}</h4>
    <div class="thumb-wrap">
      <div class="state loading">Résolution en cours</div>
    </div>
    <div class="meta">
      <a href="${pageUrl}" target="_blank" rel="noopener">Voir sur Gallica →</a>
      <span><code>${np.ark}</code></span>
    </div>`;
  return card;
}

async function fillCard(card, np, isoDate, myToken) {
  const result = await pool.run(() => resolveIssueArk(np.ark, isoDate));
  if (myToken !== renderToken) return;

  const wrap = card.querySelector(".thumb-wrap");
  const stateEl = wrap.querySelector(".state");

  if (result && typeof result === "object" && result.error) {
    stateEl.className = "state error";
    stateEl.textContent = "Erreur de résolution";
    return;
  }
  if (!result) {
    stateEl.textContent = "Numéro absent à cette date";
    return;
  }
  const issueArk = result;
  const articleUrl = `/article?ark=${encodeURIComponent(issueArk)}`
    + `&title=${encodeURIComponent(np.title)}`
    + `&date=${encodeURIComponent(isoDate)}`
    + `&catalog=${encodeURIComponent(np.ark)}`;

  const a = document.createElement("a");
  a.href = articleUrl;
  a.target = "_blank";
  a.rel = "noopener";
  a.title = `Voir la une + OCR de ${np.title}`;
  a.style.display = "block";

  const img = document.createElement("img");
  img.alt = `Une de ${np.title}`;
  img.loading = "lazy";
  img.onerror = () => { wrap.innerHTML = '<div class="state error">Vignette IIIF indisponible</div>'; };
  img.onload  = () => { stateEl.remove(); };
  img.src = iiifThumb(issueArk, 400);
  a.appendChild(img);
  wrap.appendChild(a);

  const link = card.querySelector(".meta a");
  link.href = `https://gallica.bnf.fr/ark:/12148/${issueArk}`;
  card.querySelector(".meta code").textContent = issueArk;
}

function buildListItem(np, isoDate) {
  const pageUrl  = `https://gallica.bnf.fr/ark:/12148/${np.ark}/date${compact(isoDate)}`;
  const li = document.createElement("li");
  li.innerHTML = `<a href="${pageUrl}" target="_blank" rel="noopener">${np.title}</a>
                  <span class="years">(${np.start}–${np.end === 1955 ? "…" : np.end})</span>`;
  return li;
}

function setStatus(msg, cls="") {
  const s = $("status"); s.className = cls; s.textContent = msg;
}

async function render(isoDate) {
  const myToken = ++renderToken;
  const d = new Date(isoDate + "T12:00:00");
  const year = d.getFullYear();
  $("dateInfo").innerHTML = `<strong>${fmt(d)}</strong>`;

  const active = NEWSPAPERS.filter(np => np.start <= year && year <= np.end);
  const main   = active.filter(np => np.tier === 1).sort((a,b) => a.title.localeCompare(b.title, "fr"));
  const others = active.filter(np => np.tier === 2);

  const mainGrid = $("mainGrid");
  mainGrid.innerHTML = "";
  const tasks = main.map(np => {
    const card = buildCardSkeleton(np, isoDate);
    mainGrid.appendChild(card);
    return fillCard(card, np, isoDate, myToken);
  });
  $("mainCount").textContent = main.length;

  const otherContainer = $("otherCategories");
  otherContainer.innerHTML = "";
  Object.entries(CATEGORIES).forEach(([catKey, catLabel]) => {
    const items = others.filter(np => np.cat === catKey)
                        .sort((a,b) => a.title.localeCompare(b.title, "fr"));
    if (!items.length) return;
    const div = document.createElement("div");
    div.className = "category";
    div.innerHTML = `<h3>${catLabel} <span class="count-badge">${items.length}</span></h3><ul></ul>`;
    const ul = div.querySelector("ul");
    items.forEach(np => ul.appendChild(buildListItem(np, isoDate)));
    otherContainer.appendChild(div);
  });
  $("otherCount").textContent = others.length;

  setStatus(`Résolution de ${main.length} ARK en cours…`);
  await Promise.allSettled(tasks);
  if (myToken !== renderToken) return;

  const cards = mainGrid.querySelectorAll(".card");
  let ok=0, missing=0, errors=0;
  cards.forEach(c => {
    if (c.querySelector("img")) ok++;
    else if (c.querySelector(".state.error")) errors++;
    else missing++;
  });
  setStatus(`✅ ${ok}/${main.length} vignettes • ${missing} absent(s) • ${errors} erreur(s)`,
            errors > 0 ? "error" : "ok");
}

// =============================================================================
// CONTRÔLES — date picker, timeline, raccourcis clavier
// =============================================================================
const picker = $("datePicker");
const YEAR_MIN = 1850;
const YEAR_MAX = 1955;
const DEFAULT_TARGET_YEAR = 1930; // peut être changé ici

// Date par défaut : AUJOURD'HUI (jour/mois actuels) mais à l'année cible.
// Demain ce sera automatiquement le jour suivant.
function defaultDate() {
  const today = new Date();
  const m = String(today.getMonth() + 1).padStart(2, "0");
  const d = String(today.getDate()).padStart(2, "0");
  // Cas spécial du 29 février : l'année cible n'est peut-être pas bissextile
  let iso = `${DEFAULT_TARGET_YEAR}-${m}-${d}`;
  if (m === "02" && d === "29") {
    const isLeap = (DEFAULT_TARGET_YEAR % 4 === 0 && DEFAULT_TARGET_YEAR % 100 !== 0) ||
                   (DEFAULT_TARGET_YEAR % 400 === 0);
    if (!isLeap) iso = `${DEFAULT_TARGET_YEAR}-02-28`;
  }
  return iso;
}
picker.value = defaultDate();

// Décale de N jours en gardant la même année si possible
function shiftDay(days) {
  if (typeof _cancelPickerDebounce === "function") _cancelPickerDebounce();
  const d = new Date(picker.value + "T12:00:00");
  d.setDate(d.getDate() + days);
  const iso = d.toISOString().slice(0,10);
  if (iso >= picker.min && iso <= picker.max) {
    picker.value = iso;
    render(iso);
  }
}

// Change l'année en gardant mois+jour (avec gestion du 29 février)
function goToYear(targetYear) {
  if (typeof _cancelPickerDebounce === "function") _cancelPickerDebounce();
  if (targetYear < YEAR_MIN || targetYear > YEAR_MAX) return;
  const [, m, d] = picker.value.split("-");
  let iso = `${targetYear}-${m}-${d}`;
  // Gérer 29 février sur année non bissextile
  if (m === "02" && d === "29") {
    const isLeap = (targetYear % 4 === 0 && targetYear % 100 !== 0) ||
                   (targetYear % 400 === 0);
    if (!isLeap) iso = `${targetYear}-02-28`;
  }
  picker.value = iso;
  render(iso);
}

// Debounce du date picker : on attend 3s d'inactivité avant de lancer la recherche,
// pour éviter de résoudre les dates intermédiaires pendant la saisie année/mois/jour.
const PICKER_DEBOUNCE_MS = 3000;
let _pickerTimer = null;
picker.addEventListener("change", () => {
  if (_pickerTimer) clearTimeout(_pickerTimer);
  setStatus(`Saisie en cours — recherche dans ${PICKER_DEBOUNCE_MS/1000}s…`);
  _pickerTimer = setTimeout(() => {
    _pickerTimer = null;
    render(picker.value);
  }, PICKER_DEBOUNCE_MS);
});
// Les autres déclencheurs restent immédiats (clic explicite = intention claire)
function _cancelPickerDebounce() {
  if (_pickerTimer) { clearTimeout(_pickerTimer); _pickerTimer = null; }
}
$("prevDay").addEventListener("click", () => { _cancelPickerDebounce(); shiftDay(-1); });
$("nextDay").addEventListener("click", () => { _cancelPickerDebounce(); shiftDay(+1); });
$("todayBtn").addEventListener("click", () => { _cancelPickerDebounce(); picker.value = defaultDate(); render(picker.value); });

// Raccourcis clavier : ←/→ = jour, Shift+←/→ = année, Home = date par défaut
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "ArrowLeft")  e.shiftKey ? goToYear(parseInt(picker.value.slice(0,4),10)-1) : shiftDay(-1);
  if (e.key === "ArrowRight") e.shiftKey ? goToYear(parseInt(picker.value.slice(0,4),10)+1) : shiftDay(+1);
  if (e.key === "Home") { picker.value = defaultDate(); render(picker.value); }
});

// =============================================================================
// TIMELINE — bande d'années cliquable de 1850 à 1955
// =============================================================================
const timeline = $("timeline");
const yearTicks = new Map(); // year -> <button> element

(function buildTimeline() {
  for (let y = YEAR_MIN; y <= YEAR_MAX; y++) {
    const btn = document.createElement("button");
    btn.className = "year-tick" + (y % 10 === 0 ? " decade" : "");
    btn.dataset.year = y;
    // Jour de la semaine pour la date courante à cette année
    // (mis à jour à chaque render via updateTimelineWeekdays)
    btn.innerHTML = `${y}<span class="weekday"></span>`;
    btn.addEventListener("click", () => goToYear(y));
    timeline.appendChild(btn);
    yearTicks.set(y, btn);
  }
})();

function updateTimelineActiveYear(year) {
  yearTicks.forEach((btn, y) => {
    btn.classList.toggle("active", y === year);
  });
  // Scroll horizontal pour amener l'année active au centre
  const active = yearTicks.get(year);
  if (active) {
    active.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }
}

const WEEKDAYS_SHORT = ["dim","lun","mar","mer","jeu","ven","sam"];
function updateTimelineWeekdays(isoDate) {
  const [, m, d] = isoDate.split("-").map(Number);
  yearTicks.forEach((btn, y) => {
    let date = new Date(y, m - 1, d);
    // 29 février sur non-bissextile : on saute
    if (m === 2 && d === 29 && date.getMonth() !== 1) {
      btn.querySelector(".weekday").textContent = "—";
      return;
    }
    btn.querySelector(".weekday").textContent = WEEKDAYS_SHORT[date.getDay()];
  });
}

function updateTimelineCacheMarks() {
  // Marque visuellement les années déjà en cache côté serveur
  fetch("/api/cache").then(r => r.json()).then(stats => {
    // L'endpoint ne renvoie que des stats agrégées, donc on déduit via les
    // résolutions déjà tentées : on stocke localement les années connues.
    yearTicks.forEach((btn, y) => {
      btn.classList.toggle("cached", knownCachedYears.has(y));
    });
  }).catch(() => {});
}

// Pas de préchargement : on ne résout que les unes de la date affichée
// (chaque navigation = ~17 requêtes pour les quotidiens tier 1, rien de plus).
const knownCachedYears = new Set();

// =============================================================================
// HOOK : intégrer timeline dans le rendu (sans préchargement)
// =============================================================================
const _coreRender = render;
render = function(isoDate) {
  const year = parseInt(isoDate.slice(0, 4), 10);
  updateTimelineActiveYear(year);
  updateTimelineWeekdays(isoDate);
  return _coreRender(isoDate);
};

// =============================================================================
// CIRCUIT BREAKER — bannière d'alerte si Gallica nous a bannis temporairement
// =============================================================================
const banBanner = $("ban-banner");
let banCheckInterval = null;

function formatDuration(seconds) {
  if (seconds >= 60) return `${Math.ceil(seconds / 60)} min`;
  return `${seconds}s`;
}

async function checkBanStatus() {
  try {
    const r = await fetch("/api/status");
    const d = await r.json();
    if (d.banned) {
      banBanner.style.display = "";
      banBanner.innerHTML = `⛔ <strong>IP temporairement bloquée par Gallica</strong> — `
        + `reprise automatique à <strong>${d.retry_at}</strong> `
        + `(dans ${formatDuration(d.retry_in_seconds)}). `
        + `Les nouvelles résolutions sont suspendues.`;
      if (!banCheckInterval) {
        banCheckInterval = setInterval(checkBanStatus, 30000);
      }
    } else {
      banBanner.style.display = "none";
      if (banCheckInterval) { clearInterval(banCheckInterval); banCheckInterval = null; }
    }
  } catch (_) {}
}

// Vérifier au chargement, puis toutes les 30s si ban actif
checkBanStatus();

render(picker.value);
</script>
</body>
</html>
"""

# =============================================================================
# PAGE ARTICLE — servie à /article?ark=bpt6k…&title=…&date=…&catalog=cb…
# =============================================================================
ARTICLE_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Article — Gallica BnF</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/openseadragon.min.js"></script>
<style>
  :root {
    --bg:#f4f0e8; --paper:#fff; --ink:#1a1a1a; --muted:#666;
    --accent:#8b2c2c; --link:#1a4a7a; --ok:#2d6a4f;
  }
  * { box-sizing: border-box; }
  body { font-family: Georgia, "Times New Roman", serif; background: var(--bg);
         margin: 0; padding: 0; color: var(--ink); min-height: 100vh; }

  .top-bar { background: var(--ink); color: white; padding: .5rem 1.5rem;
             display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
  .top-bar a { color: #ccc; text-decoration: none; font-size: .9rem; }
  .top-bar a:hover { color: white; }
  .top-bar .sep { color: #555; }
  .top-bar h1 { margin: 0; font-size: 1.1rem; font-weight: bold; flex: 1;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .top-bar .date-badge { font-size: .85rem; color: #bbb; white-space: nowrap; }

  .viewer { display: grid; grid-template-columns: 1fr 1fr; min-height: calc(100vh - 44px); }

  .image-panel { background: #111; position: sticky; top: 0;
                 height: 100vh; overflow: hidden; }
  /* OSD crée son propre canvas ; on surcharge sa couleur de fond */
  .openseadragon-container { background: #111 !important; }
  .img-msg { color: #666; font-size: .9rem; font-style: italic;
             padding: 3rem; text-align: center; }

  .text-panel { padding: 1.5rem; overflow-y: auto; display: flex;
                flex-direction: column; gap: 1rem; }

  .section { background: var(--paper); padding: 1.2rem 1.4rem;
             box-shadow: 0 2px 6px rgba(0,0,0,.08); }
  .section h2 { margin: 0 0 .7rem 0; font-size: 1rem; color: var(--accent);
                border-bottom: 1px solid #eee; padding-bottom: .3rem; }
  .section p { margin: 0 0 .4rem 0; line-height: 1.6; }
  .note { font-size: .8rem; color: var(--muted); font-style: italic; }

  .btn { font-family: inherit; padding: .45rem 1rem; cursor: pointer;
         background: var(--ink); color: white; border: none; font-size: .9rem; }
  .btn:hover { background: var(--accent); }
  .btn:disabled { background: #888; cursor: default; }

  .ocr-box { font-family: "Courier New", Courier, monospace; font-size: .78rem;
             line-height: 1.55; white-space: pre-wrap; word-break: break-word;
             background: #fafafa; border: 1px solid #ddd; padding: .8rem;
             max-height: 55vh; overflow-y: auto; margin-top: .7rem; }

  a.gal-link { color: var(--link); text-decoration: none; font-size: .9rem; }
  a.gal-link:hover { text-decoration: underline; }

  .steps-row { display: flex; gap: 2rem; margin: .6rem 0 .5rem; }
  .step-item { display: flex; align-items: center; gap: .4rem; font-size: .82rem;
               color: var(--muted); transition: color .3s, opacity .3s; opacity: .45; }
  .step-item .dot { width: 8px; height: 8px; border-radius: 50%;
                    background: currentColor; flex-shrink: 0; }
  .step-item.active { color: var(--accent); opacity: 1; font-weight: bold; }
  .step-item.done   { color: var(--ok);     opacity: .85; }
  .bar-track { height: 5px; background: #ddd; border-radius: 3px; overflow: hidden; }
  .bar-fill  { height: 100%; width: 0%; border-radius: 3px;
               background: var(--accent); transition: width .55s ease-out; }
  .bar-fill.done { background: var(--ok); }
  .step-label { font-size: .78rem; color: var(--muted); margin-top: .35rem;
                font-style: italic; min-height: 1.1em; }

  /* Overlay blocs */
  #imgWrapper { position: relative; display: inline-block; line-height: 0; }
  #imgWrapper img { display: block; max-width: 100%; height: auto; }
  #blockOverlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
  .block-zone { position: absolute; cursor: pointer; box-sizing: border-box;
                border: 1.5px solid transparent;
                background: var(--block-bg, transparent);
                transition: background .15s, border-color .15s; }
  .block-zone:hover  { background: var(--hover-bg,  rgba(139,44,44,.45)) !important; }
  .block-zone.active { background: var(--active-bg, rgba(139,44,44,.65)) !important;
                       border-width: 2px; }
  .block-num { position: absolute; top: 1px; left: 2px; font-size: 9px;
               background: var(--accent); color: #fff; padding: 0 3px;
               line-height: 1.4; opacity: .75; pointer-events: none; }
  .block-zone.active .block-num { opacity: 1; }
  #selectedText { font-family: "Courier New", monospace; font-size: .78rem;
                  line-height: 1.6; white-space: pre-wrap; word-break: break-word;
                  background: #fafafa; border: 1px solid #ddd; padding: .8rem;
                  max-height: 40vh; overflow-y: auto; margin-top: .5rem; }
  #blockHint { font-size: .8rem; color: var(--muted); font-style: italic; margin-top: .4rem; }

  /* Mode dessin de zone */
  .osd-draw-mode { cursor: crosshair !important; }
  .osd-draw-mode .block-zone { pointer-events: none !important; }
  #drawRectEl { position: absolute; border: 2px solid #e74c3c;
                background: rgba(231,76,60,.1); pointer-events: none;
                box-sizing: border-box; }
  .btn.active-draw { background: var(--accent) !important; }
  #krakenLoading { display: flex; align-items: center; gap: .5rem;
                   font-size: .82rem; color: var(--muted); font-style: italic;
                   margin-top: .4rem; }
  .spinner { width: 14px; height: 14px; border: 2px solid #ddd;
             border-top-color: var(--accent); border-radius: 50%;
             animation: spin .7s linear infinite; flex-shrink: 0; }
  @keyframes spin { to { transform: rotate(360deg); } }

  @media (max-width: 800px) {
    .viewer { grid-template-columns: 1fr; }
    .image-panel { position: static; height: auto; max-height: 60vh; }
  }
</style>
</head>
<body>

<div class="top-bar">
  <a href="javascript:history.back()">← Retour</a>
  <span class="sep">|</span>
  <h1 id="journalTitle">Chargement…</h1>
  <span class="date-badge" id="journalDate"></span>
</div>

<div class="viewer">
  <div class="image-panel" id="imagePanel">
    <p class="img-msg">Chargement de l'image…</p>
  </div>

  <div class="text-panel">

    <div class="section" id="metaSection" style="display:none">
      <h2>Présentation BnF</h2>
      <p id="metaDescription"></p>
      <p class="note" id="metaPublisher"></p>
    </div>

    <div class="section" id="ocrSection">
      <h2>Texte OCR</h2>
      <div id="ocrPending">
        <button class="btn" id="ocrBtn" onclick="extractOCR()">Extraire l'OCR</button>
        <p class="note" style="margin-top:.5rem">
          Télécharge la une en haute résolution et lance l'OCR local (Tesseract).
          Peut prendre 10–30 secondes. Le résultat est mis en cache.
        </p>
      </div>
      <div id="ocrLoading" style="display:none; margin-top:.5rem">
        <div class="steps-row">
          <div class="step-item" id="step-download"><div class="dot"></div>Téléchargement</div>
          <div class="step-item" id="step-ocr"><div class="dot"></div>Analyse OCR</div>
        </div>
        <div class="bar-track"><div class="bar-fill" id="progressBar"></div></div>
        <p class="step-label" id="stepLabel">Démarrage…</p>
      </div>
      <div id="ocrResult" style="display:none">
        <div class="ocr-box" id="ocrText"></div>
      </div>
      <div id="ocrError" style="display:none">
        <p class="note" id="ocrErrMsg" style="color:#a00"></p>
        <p style="margin-top:.6rem">
          <a class="gal-link" id="ocrViewerLink" href="#" target="_blank" rel="noopener">
            📖 Ouvrir dans le viewer Gallica →
          </a>
        </p>
      </div>
    </div>

    <div class="section" id="krakenFullSection">
      <h2>OCR pleine page (Kraken)</h2>
      <div id="kfPending">
        <button class="btn" id="kfBtn" onclick="runKrakenFull()">Lancer l'OCR Kraken</button>
        <p class="note" style="margin-top:.5rem">
          Segmentation neurale blla + reconnaissance CATMuS-Print sur toute la une.
          Comptez 2–5 minutes. Le résultat est mis en cache.
        </p>
      </div>
      <div id="kfLoading" style="display:none; margin-top:.5rem">
        <div class="steps-row">
          <div class="step-item" id="kfstep-dl"><div class="dot"></div>Téléchargement</div>
          <div class="step-item" id="kfstep-seg"><div class="dot"></div>Segmentation blla</div>
          <div class="step-item" id="kfstep-rec"><div class="dot"></div>Reconnaissance</div>
        </div>
        <div class="bar-track"><div class="bar-fill" id="kfBar"></div></div>
        <p class="step-label" id="kfLabel">Démarrage…</p>
      </div>
      <div id="kfError" style="display:none">
        <p class="note" id="kfErrMsg" style="color:#a00"></p>
      </div>
      <div id="kfResult" style="display:none">
        <p class="note" id="kfStats" style="margin-bottom:.4rem"></p>
        <div class="ocr-box" id="kfText"></div>
      </div>
    </div>

    <div class="section" id="blocksSection">
      <h2>Blocs Paddle + Kraken OCR</h2>
      <div id="blocksPending">
        <button class="btn" id="blocksBtn" onclick="loadBlocks()">Analyser les blocs (Paddle)</button>
        <p class="note" style="margin-top:.5rem">
          Détecte les blocs de texte via PaddleOCR.
          Cliquez ensuite sur un bloc pour lancer l'OCR Kraken CATMuS-Print.
        </p>
      </div>
      <div id="blocksLoading" style="display:none; margin-top:.5rem">
        <div class="steps-row">
          <div class="step-item" id="bstep-download"><div class="dot"></div>Téléchargement</div>
          <div class="step-item" id="bstep-ocr"><div class="dot"></div>Analyse Paddle</div>
        </div>
        <div class="bar-track"><div class="bar-fill" id="blocksBar"></div></div>
        <p class="step-label" id="blocksLabel">Démarrage…</p>
      </div>
      <div id="blocksError" style="display:none">
        <p class="note" id="blocksErrMsg" style="color:#a00"></p>
      </div>
      <div id="blocksResult" style="display:none">
        <p id="blockHint">Cliquez sur un bloc pour lancer l'OCR Kraken.</p>
        <div id="krakenLoading" style="display:none">
          <div class="spinner"></div>
          <span id="krakenLoadingMsg">OCR Kraken en cours…</span>
        </div>
        <div id="selectedText" style="display:none"></div>
      </div>
    </div>

    <div class="section" id="drawSection">
      <h2>OCR zone libre (Kraken)</h2>
      <div>
        <button class="btn" id="drawBtn" onclick="toggleDraw()">Dessiner une zone</button>
        <p class="note" style="margin-top:.5rem">
          Tracez un rectangle sur l'image pour OCR-iser cette zone avec Kraken CATMuS-Print.
          L'image est téléchargée si nécessaire.
        </p>
      </div>
      <div id="drawLoading" style="display:none; margin-top:.5rem">
        <div id="krakenLoading2" style="display:flex;align-items:center;gap:.5rem">
          <div class="spinner"></div>
          <span>OCR Kraken en cours…</span>
        </div>
      </div>
      <div id="drawError" style="display:none">
        <p class="note" id="drawErrMsg" style="color:#a00"></p>
      </div>
      <div id="drawResult" style="display:none">
        <div class="ocr-box" id="drawText"></div>
      </div>
    </div>

    <div class="section">
      <h2>Liens</h2>
      <p><a class="gal-link" id="linkIssue" href="#" target="_blank" rel="noopener">Ouvrir ce numéro sur Gallica →</a></p>
      <p><a class="gal-link" id="linkCal"   href="#" target="_blank" rel="noopener">Calendrier du titre sur Gallica →</a></p>
    </div>

  </div>
</div>

<script>
const MONTHS = ["janvier","février","mars","avril","mai","juin",
                "juillet","août","septembre","octobre","novembre","décembre"];

const p       = new URLSearchParams(location.search);
const issueArk  = p.get("ark")     || "";
const title     = p.get("title")   || "Journal";
const isoDate   = p.get("date")    || "";
const catalogArk = p.get("catalog") || "";

document.title = `${title} — ${isoDate}`;
document.getElementById("journalTitle").textContent = title;
const [yr, mo, dy] = isoDate.split("-");
document.getElementById("journalDate").textContent =
  (dy && mo && yr) ? `${parseInt(dy)} ${MONTHS[parseInt(mo)-1]} ${yr}` : isoDate;

// Liens Gallica
const yyyymmdd = isoDate.replace(/-/g, "");
document.getElementById("linkIssue").href =
  issueArk ? `https://gallica.bnf.fr/ark:/12148/${issueArk}` : "#";
document.getElementById("linkCal").href =
  catalogArk ? `https://gallica.bnf.fr/ark:/12148/${catalogArk}/date${yyyymmdd}` : "#";

// ── OpenSeadragon — viewer IIIF tuilé (qualité native) ────────────────────
// Chaque tuile 512px est servie à meilleure qualité que le JPEG pleine page.

let _osdViewer = null;
let _pendingBlocks = null;

if (issueArk) {
  _osdViewer = OpenSeadragon({
    element:               document.getElementById("imagePanel"),
    tileSources:           `/api/iiif-info?ark=${encodeURIComponent(issueArk)}`,
    showNavigator:         false,
    showNavigationControl: false,
    showRotationControl:   false,
    animationTime:         0.2,
    springStiffness:       14,
    maxZoomPixelRatio:     4,
    minZoomLevel:          0.05,
    visibilityRatio:       0.15,
    defaultZoomLevel:      0,
    constrainDuringPan:    false,
    imageLoaderLimit:      4,
    preload:               true,
    prefixUrl:             "",
    gestureSettingsMouse:  { scrollToZoom: true, clickToZoom: false,
                             dblClickToZoom: false, dragToPan: true },
  });

  _osdViewer.addHandler("open", () => {
    if (_pendingBlocks) { _doRenderBlocks(_pendingBlocks); _pendingBlocks = null; }
  });

  _osdViewer.addHandler("canvas-press", (e) => {
    if (!_drawMode) return;
    e.preventDefaultAction = true;
    _drawStart = { x: e.position.x, y: e.position.y };
    if (_drawRectEl) _drawRectEl.remove();
    _drawRectEl = document.createElement("div");
    _drawRectEl.id = "drawRectEl";
    _drawRectEl.style.left = e.position.x + "px";
    _drawRectEl.style.top  = e.position.y + "px";
    _drawRectEl.style.width = "0"; _drawRectEl.style.height = "0";
    _osdViewer.element.appendChild(_drawRectEl);
  });

  _osdViewer.addHandler("canvas-drag", (e) => {
    if (!_drawMode || !_drawRectEl || !_drawStart) return;
    e.preventDefaultAction = true;
    const x = Math.min(_drawStart.x, e.position.x);
    const y = Math.min(_drawStart.y, e.position.y);
    _drawRectEl.style.left   = x + "px";
    _drawRectEl.style.top    = y + "px";
    _drawRectEl.style.width  = Math.abs(e.position.x - _drawStart.x) + "px";
    _drawRectEl.style.height = Math.abs(e.position.y - _drawStart.y) + "px";
  });

  _osdViewer.addHandler("canvas-release", async (e) => {
    if (!_drawMode || !_drawStart) return;
    const item = _osdViewer.world.getItemAt(0);
    if (!item) return;
    const sz = item.getContentSize();
    const clamp = v => Math.max(0, Math.min(1, v));
    const toNorm = (px, py) => {
      const vp = _osdViewer.viewport.pointFromPixel(new OpenSeadragon.Point(px, py));
      const ip = item.viewportToImageCoordinates(vp);
      return { x: clamp(ip.x / sz.x), y: clamp(ip.y / sz.y) };
    };
    const p0 = toNorm(Math.min(_drawStart.x, e.position.x), Math.min(_drawStart.y, e.position.y));
    const p1 = toNorm(Math.max(_drawStart.x, e.position.x), Math.max(_drawStart.y, e.position.y));
    if (_drawRectEl) { _drawRectEl.remove(); _drawRectEl = null; }
    _drawStart = null;
    toggleDraw();
    if (p1.x - p0.x < 0.01 || p1.y - p0.y < 0.005) return;

    document.getElementById("drawLoading").style.display = "";
    document.getElementById("drawResult").style.display  = "none";
    document.getElementById("drawError").style.display   = "none";
    try {
      _drawAbort = new AbortController();
      const url = `/api/ocr-kraken-region?ark=${encodeURIComponent(issueArk)}`
        + `&x0=${p0.x.toFixed(4)}&y0=${p0.y.toFixed(4)}&x1=${p1.x.toFixed(4)}&y1=${p1.y.toFixed(4)}`;
      const r = await fetch(url, { signal: _drawAbort.signal });
      const d = await r.json();
      document.getElementById("drawLoading").style.display = "none";
      if (d.error) {
        document.getElementById("drawErrMsg").textContent = d.error;
        document.getElementById("drawError").style.display = "";
      } else {
        document.getElementById("drawText").textContent = d.text;
        document.getElementById("drawResult").style.display = "";
      }
    } catch (err) {
      document.getElementById("drawLoading").style.display = "none";
      document.getElementById("drawErrMsg").textContent = `Erreur réseau : ${err}`;
      document.getElementById("drawError").style.display = "";
    }
  });

  _osdViewer.addHandler("canvas-double-click", (e) => {
    if (_drawMode) e.preventDefaultAction = true;
  });
}

// Métadonnées BnF — chargement automatique silencieux
async function loadMeta() {
  if (!issueArk) return;
  try {
    const r = await fetch(`/api/meta?ark=${encodeURIComponent(issueArk)}`);
    const d = await r.json();
    if (d.error) return;
    const hasContent = d.title || d.publisher;
    if (!hasContent) return;
    if (d.title) document.getElementById("metaDescription").textContent = d.title;
    if (d.publisher) document.getElementById("metaPublisher").textContent = `Éditeur : ${d.publisher}`;
    document.getElementById("metaSection").style.display = "";
  } catch (_) {}
}

// Lien viewer Gallica (fallback en cas d'erreur OCR)
if (issueArk) {
  document.getElementById("ocrViewerLink").href =
    `https://gallica.bnf.fr/ark:/12148/${issueArk}/f1.item`;
}

let _barTimer = null;

function setBarWidth(pct, animate) {
  const bar = document.getElementById("progressBar");
  if (!animate) bar.style.transition = "none";
  bar.style.width = pct + "%";
  if (!animate) void bar.offsetWidth; // force reflow
  if (!animate) bar.style.transition = "";
}

function animateBar(from, to, durationMs) {
  if (_barTimer) clearInterval(_barTimer);
  const bar = document.getElementById("progressBar");
  setBarWidth(from, false);
  const fps = 30, interval = 1000 / fps;
  const steps = durationMs / interval;
  const inc = (to - from) / steps;
  let cur = from;
  _barTimer = setInterval(() => {
    cur = Math.min(to, cur + inc);
    bar.style.width = cur + "%";
    if (cur >= to) clearInterval(_barTimer);
  }, interval);
}

function setStep(id, state) {
  const el = document.getElementById("step-" + id);
  el.classList.remove("active", "done");
  if (state) el.classList.add(state);
}

function setLabel(msg) {
  document.getElementById("stepLabel").textContent = msg;
}

function ocrError(msg) {
  if (_barTimer) clearInterval(_barTimer);
  document.getElementById("ocrLoading").style.display = "none";
  document.getElementById("ocrErrMsg").textContent = msg;
  document.getElementById("ocrError").style.display = "";
}

// ── Exclusion mutuelle des modes OCR ─────────────────────────────────────
let _ocrAbort = null, _kfAbort = null, _blocksAbort = null, _drawAbort = null;

function _resetOcr() {
  if (_ocrAbort) { _ocrAbort.abort(); _ocrAbort = null; }
  if (_barTimer) clearInterval(_barTimer);
  document.getElementById("ocrPending").style.display = "";
  document.getElementById("ocrLoading").style.display = "none";
  document.getElementById("ocrResult").style.display  = "none";
  document.getElementById("ocrError").style.display   = "none";
  document.getElementById("ocrBtn").disabled = false;
}
function _resetKrakenFull() {
  if (_kfAbort) { _kfAbort.abort(); _kfAbort = null; }
  if (_kfBarTimer) clearInterval(_kfBarTimer);
  document.getElementById("kfPending").style.display = "";
  document.getElementById("kfLoading").style.display = "none";
  document.getElementById("kfResult").style.display  = "none";
  document.getElementById("kfError").style.display   = "none";
  document.getElementById("kfBtn").disabled = false;
}
function _resetBlocks() {
  if (_blocksAbort) { _blocksAbort.abort(); _blocksAbort = null; }
  if (_bBarTimer) clearInterval(_bBarTimer);
  if (_osdViewer) _osdViewer.clearOverlays();
  document.getElementById("blocksPending").style.display = "";
  document.getElementById("blocksLoading").style.display = "none";
  document.getElementById("blocksResult").style.display  = "none";
  document.getElementById("blocksError").style.display   = "none";
  document.getElementById("blocksBtn").disabled = false;
  document.getElementById("selectedText").style.display  = "none";
  document.getElementById("krakenLoading").style.display = "none";
}
function _resetDraw() {
  if (_drawAbort) { _drawAbort.abort(); _drawAbort = null; }
  if (_drawMode) {
    _drawMode = false;
    const btn = document.getElementById("drawBtn");
    btn.textContent = "Dessiner une zone";
    btn.classList.remove("active-draw");
    if (_drawRectEl) { _drawRectEl.remove(); _drawRectEl = null; }
    _drawStart = null;
    if (_osdViewer) {
      _osdViewer.setMouseNavEnabled(true);
      _osdViewer.element.classList.remove("osd-draw-mode");
    }
  }
  document.getElementById("drawLoading").style.display = "none";
  document.getElementById("drawResult").style.display  = "none";
  document.getElementById("drawError").style.display   = "none";
}

async function extractOCR() {
  _resetBlocks(); _resetDraw(); _resetKrakenFull();
  _ocrAbort = new AbortController();
  const _ocrSig = _ocrAbort.signal;
  document.getElementById("ocrBtn").disabled = true;
  document.getElementById("ocrPending").style.display = "none";
  document.getElementById("ocrLoading").style.display  = "";

  // Étape 1 : téléchargement
  setStep("download", "active");
  setLabel("Téléchargement de l'image haute résolution…");
  animateBar(0, 45, 6000);

  try {
    const r1 = await fetch(`/api/ocr-download?ark=${encodeURIComponent(issueArk)}`, { signal: _ocrSig });
    const d1 = await r1.json();
    if (d1.error) { ocrError(d1.error); return; }

    setStep("download", "done");
    setStep("ocr", "active");
    setLabel("Analyse OCR en cours (Tesseract)…");
    animateBar(50, 92, 28000);

    // Étape 2 : OCR
    const r2 = await fetch(`/api/ocr-local?ark=${encodeURIComponent(issueArk)}`, { signal: _ocrSig });
    const d2 = await r2.json();

    if (d2.error) { ocrError(d2.error); return; }

    // Succès
    if (_barTimer) clearInterval(_barTimer);
    setStep("ocr", "done");
    setLabel("Terminé !");
    setBarWidth(100, true);
    document.getElementById("progressBar").classList.add("done");

    setTimeout(() => {
      document.getElementById("ocrLoading").style.display = "none";
      document.getElementById("ocrText").textContent = d2.text;
      document.getElementById("ocrResult").style.display = "";
    }, 500);

  } catch (e) {
    if (e.name !== 'AbortError') ocrError(`Erreur réseau : ${e}`);
  }
}

loadMeta();

// ── OCR pleine page Kraken ─────────────────────────────────────────────────

let _kfBarTimer = null;

function _kfAnimate(from, to, ms) {
  if (_kfBarTimer) clearInterval(_kfBarTimer);
  const bar = document.getElementById("kfBar");
  bar.style.transition = "none"; bar.style.width = from + "%";
  void bar.offsetWidth; bar.style.transition = "";
  const fps = 30, iv = 1000 / fps, inc = (to - from) / (ms / iv);
  let cur = from;
  _kfBarTimer = setInterval(() => {
    cur = Math.min(to, cur + inc);
    bar.style.width = cur + "%";
    if (cur >= to) clearInterval(_kfBarTimer);
  }, iv);
}

function _kfSetStep(id, state) {
  const el = document.getElementById("kfstep-" + id);
  el.classList.remove("active", "done");
  if (state) el.classList.add(state);
}

function _kfError(msg) {
  if (_kfBarTimer) clearInterval(_kfBarTimer);
  document.getElementById("kfLoading").style.display = "none";
  document.getElementById("kfErrMsg").textContent = msg;
  document.getElementById("kfError").style.display = "";
}

async function runKrakenFull() {
  _resetBlocks(); _resetDraw(); _resetOcr();
  _kfAbort = new AbortController();
  const _kfSig = _kfAbort.signal;
  document.getElementById("kfBtn").disabled = true;
  document.getElementById("kfPending").style.display = "none";
  document.getElementById("kfLoading").style.display = "";

  _kfSetStep("dl", "active");
  document.getElementById("kfLabel").textContent = "Téléchargement de l'image…";
  _kfAnimate(0, 15, 5000);

  try {
    const r1 = await fetch(`/api/ocr-download?ark=${encodeURIComponent(issueArk)}`, { signal: _kfSig });
    const d1 = await r1.json();
    if (d1.error) { _kfError(d1.error); return; }

    _kfSetStep("dl", "done"); _kfSetStep("seg", "active");
    document.getElementById("kfLabel").textContent = "Segmentation des lignes (blla) + reconnaissance…";
    _kfAnimate(18, 90, 240000); // 4 min estimées

    const r2 = await fetch(`/api/ocr-kraken-full?ark=${encodeURIComponent(issueArk)}`, { signal: _kfSig });
    const d2 = await r2.json();
    if (d2.error) { _kfError(d2.error); return; }

    if (_kfBarTimer) clearInterval(_kfBarTimer);
    _kfSetStep("seg", "done"); _kfSetStep("rec", "done");
    document.getElementById("kfBar").style.width = "100%";
    document.getElementById("kfBar").classList.add("done");
    document.getElementById("kfLabel").textContent = `Terminé${d2.cached ? " (cache)" : ""} — ${d2.n_lines} lignes`;

    setTimeout(() => {
      document.getElementById("kfLoading").style.display = "none";
      document.getElementById("kfStats").textContent =
        `${d2.n_lines} lignes reconnues${d2.cached ? " (résultat en cache)" : ""}`;
      document.getElementById("kfText").textContent = d2.text;
      document.getElementById("kfResult").style.display = "";
    }, 400);

  } catch (e) { if (e.name !== 'AbortError') _kfError(`Erreur réseau : ${e}`); }
}

// ── Blocs interactifs ──────────────────────────────────────────────────────

let _bBarTimer = null;
function bAnimateBar(from, to, ms) {
  if (_bBarTimer) clearInterval(_bBarTimer);
  const bar = document.getElementById("blocksBar");
  bar.style.transition = "none"; bar.style.width = from + "%";
  void bar.offsetWidth; bar.style.transition = "";
  const fps = 30, iv = 1000 / fps, inc = (to - from) / (ms / iv);
  let cur = from;
  _bBarTimer = setInterval(() => {
    cur = Math.min(to, cur + inc);
    bar.style.width = cur + "%";
    if (cur >= to) clearInterval(_bBarTimer);
  }, iv);
}
function bSetStep(id, state) {
  const el = document.getElementById("bstep-" + id);
  el.classList.remove("active", "done");
  if (state) el.classList.add(state);
}
function bSetLabel(msg) { document.getElementById("blocksLabel").textContent = msg; }

async function loadBlocks() {
  _resetDraw(); _resetKrakenFull(); _resetOcr();
  _blocksAbort = new AbortController();
  const _blocksSig = _blocksAbort.signal;
  document.getElementById("blocksBtn").disabled = true;
  document.getElementById("blocksPending").style.display = "none";
  document.getElementById("blocksLoading").style.display = "";

  bSetStep("download", "active");
  bSetLabel("Téléchargement de l'image haute résolution…");
  bAnimateBar(0, 45, 6000);

  try {
    const r1 = await fetch(`/api/ocr-download?ark=${encodeURIComponent(issueArk)}`, { signal: _blocksSig });
    const d1 = await r1.json();
    if (d1.error) { bError(d1.error); return; }

    bSetStep("download", "done"); bSetStep("ocr", "active");
    bSetLabel("Détection des blocs (PaddleOCR)…");
    bAnimateBar(50, 92, 30000);

    const r2 = await fetch(`/api/layout-blocks?ark=${encodeURIComponent(issueArk)}`, { signal: _blocksSig });
    const d2 = await r2.json();
    if (d2.error) { bError(d2.error); return; }

    if (_bBarTimer) clearInterval(_bBarTimer);
    const nb = d2.blocks.length;
    bSetStep("ocr", "done");
    bSetLabel(`${nb} blocs détectés`);
    document.getElementById("blocksBar").style.width = "100%";
    document.getElementById("blocksBar").classList.add("done");

    setTimeout(() => {
      document.getElementById("blocksLoading").style.display = "none";
      document.getElementById("blocksResult").style.display = "";
      renderBlocks(d2.blocks);
    }, 400);

  } catch (e) { if (e.name !== 'AbortError') bError(`Erreur réseau : ${e}`); }
}

function bError(msg) {
  if (_bBarTimer) clearInterval(_bBarTimer);
  document.getElementById("blocksLoading").style.display = "none";
  document.getElementById("blocksErrMsg").textContent = msg;
  document.getElementById("blocksError").style.display = "";
}

const LABEL_FR = {
  Title: "Titre", "Section-header": "Sous-titre",
  SectionHeader: "Titre", Text: "Texte", Caption: "Légende",
  Picture: "Image", Figure: "Figure", Table: "Tableau",
  PageHeader: "En-tête", PageFooter: "Pied de page",
  Footnote: "Note", Bibliography: "Bibliographie", Code: "Code",
};

function _doRenderBlocks(blocks) {
  if (!_osdViewer) return;
  _osdViewer.clearOverlays();
  const item = _osdViewer.world.getItemAt(0);
  if (!item) { _pendingBlocks = blocks; return; }
  const sz = item.getContentSize();
  blocks.forEach((block, i) => {
    const color = block.color || "#888";
    const div = document.createElement("div");
    div.className = "block-zone";
    div.style.borderColor = color + "cc";
    div.style.setProperty("--block-bg",  color + "38");
    div.style.setProperty("--hover-bg",  color + "66");
    div.style.setProperty("--active-bg", color + "99");
    const lbl = document.createElement("div");
    lbl.className = "block-num";
    lbl.style.background = color;
    lbl.textContent = `${i + 1} · ${LABEL_FR[block.label] || block.label}`;
    div.appendChild(lbl);
    div.addEventListener("mousedown", (e) => e.stopPropagation());
    div.addEventListener("dblclick",  (e) => e.stopPropagation());
    div.addEventListener("click",     (e) => { e.stopPropagation(); selectBlock(div, block, i + 1); });
    const rect = item.imageToViewportRectangle(
      block.x0 * sz.x, block.y0 * sz.y,
      (block.x1 - block.x0) * sz.x, (block.y1 - block.y0) * sz.y
    );
    _osdViewer.addOverlay({ element: div, location: rect });
  });
}

function renderBlocks(blocks) {
  if (!_osdViewer || !_osdViewer.isOpen()) { _pendingBlocks = blocks; return; }
  _doRenderBlocks(blocks);
}


async function selectBlock(el, block, num) {
  document.querySelectorAll(".block-zone").forEach(z => z.classList.remove("active"));
  el.classList.add("active");
  const labelFr = LABEL_FR[block.label] || block.label;
  const confStr = block.confidence ? ` (conf. ${Math.round(block.confidence * 100)}%)` : "";
  document.getElementById("blockHint").textContent = `Bloc ${num} — ${labelFr}${confStr}`;
  const box = document.getElementById("selectedText");
  const loader = document.getElementById("krakenLoading");
  box.style.display = "none";
  loader.style.display = "flex";
  document.getElementById("krakenLoadingMsg").textContent = "OCR Kraken en cours…";
  try {
    const url = `/api/ocr-kraken-region?ark=${encodeURIComponent(issueArk)}`
      + `&x0=${block.x0}&y0=${block.y0}&x1=${block.x1}&y1=${block.y1}`;
    const r = await fetch(url);
    const d = await r.json();
    loader.style.display = "none";
    if (d.error) {
      box.textContent = `Erreur Kraken : ${d.error}`;
    } else {
      box.textContent = d.text;
      document.getElementById("blockHint").textContent =
        `Bloc ${num} — ${labelFr}${confStr} — Kraken (${d.n_lines || "?"} ligne${d.n_lines !== 1 ? "s" : ""})`;
    }
    box.style.display = "";
  } catch (e) {
    loader.style.display = "none";
    box.textContent = `Erreur réseau : ${e}`;
    box.style.display = "";
  }
}

// ── Mode dessin rectangle (OSD) ──────────────────────────────────────────

let _drawMode = false, _drawStart = null, _drawRectEl = null;

function toggleDraw() {
  _drawMode = !_drawMode;
  const btn = document.getElementById("drawBtn");
  if (_drawMode) {
    _resetBlocks(); _resetKrakenFull(); _resetOcr();
    btn.textContent = "Annuler";
    btn.classList.add("active-draw");
    if (_osdViewer) {
      _osdViewer.setMouseNavEnabled(false);
      _osdViewer.element.classList.add("osd-draw-mode");
    }
  } else {
    btn.textContent = "Dessiner une zone";
    btn.classList.remove("active-draw");
    if (_drawRectEl) { _drawRectEl.remove(); _drawRectEl = null; }
    _drawStart = null;
    if (_osdViewer) {
      _osdViewer.setMouseNavEnabled(true);
      _osdViewer.element.classList.remove("osd-draw-mode");
    }
  }
}
</script>
</body>
</html>
"""

# =============================================================================
# HANDLER HTTP
# =============================================================================
class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._send(200, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
            return

        if parsed.path == "/api/resolve":
            params = urllib.parse.parse_qs(parsed.query)
            ark  = (params.get("ark")  or [""])[0]
            date = (params.get("date") or [""])[0]
            result = self._resolve(ark, date)
            if VERBOSE:
                print(f"  [resolve] {ark} {date} → {result}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result).encode("utf-8"))
            return

        if parsed.path == "/api/status":
            remaining = max(0.0, _ban_until[0] - time.time())
            with _tb_lock:
                tokens = round(_tb_tokens[0], 1)
            payload = {
                "banned": remaining > 0,
                "retry_in_seconds": int(remaining),
                "retry_at": time.strftime("%H:%M:%S", time.localtime(_ban_until[0])) if remaining > 0 else None,
                "rate_tokens": tokens,
                "rate_capacity": TB_CAPACITY,
                "has_tesseract": TESSERACT_OK,
                "has_surya": SURYA_OK,
                "has_paddle": PADDLE_OK,
            }
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(payload).encode("utf-8"))
            return

        if parsed.path == "/api/cache":
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("clear"):
                with DATE_CACHE_LOCK:
                    n = len(DATE_CACHE)
                    DATE_CACHE.clear()
                self._send(200, "application/json", json.dumps({"cleared_dates": n}).encode())
            else:
                with DATE_CACHE_LOCK:
                    n_dates = len(DATE_CACHE)
                    n_found = sum(1 for v in DATE_CACHE.values() if v)
                with _tb_lock:
                    tokens = round(_tb_tokens[0], 1)
                stats = {
                    "dates_cached": n_dates,
                    "dates_with_issue": n_found,
                    "rate_tokens": tokens,
                    "rate_capacity": TB_CAPACITY,
                }
                self._send(200, "application/json", json.dumps(stats).encode())
            return

        if parsed.path == "/article":
            self._send(200, "text/html; charset=utf-8", ARTICLE_PAGE.encode("utf-8"))
            return

        if parsed.path == "/api/ocr":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            result = self._get_ocr(ark)
            if VERBOSE:
                cached = result.get("cached", False)
                print(f"  [ocr] {ark} → {result.get('length', 'err')} chars {'(cache)' if cached else '(fetch)'}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/ocr-download":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            result = download_image(ark, IMG_CACHE_DIR)
            if VERBOSE:
                print(f"  [ocr-dl] {ark} → {'cache' if result.get('cached') else 'téléchargé' if result.get('ok') else result.get('error','err')}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/ocr-local":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            result = run_ocr(ark, OCR_CACHE_DIR, IMG_CACHE_DIR)
            if VERBOSE:
                cached = result.get("cached", False)
                print(f"  [ocr-local] {ark} → {result.get('length', 'err')} chars "
                      f"{'(cache)' if cached else '(tesseract)'}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/ocr-blocks":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            result = run_ocr_blocks(ark, OCR_CACHE_DIR, IMG_CACHE_DIR)
            if VERBOSE:
                cached = result.get("cached", False)
                n = len(result.get("blocks", []))
                print(f"  [ocr-blocks] {ark} → {n} blocs {'(cache)' if cached else '(tesseract)'}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/layout-blocks":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            result = run_layout_blocks(ark, OCR_CACHE_DIR, IMG_CACHE_DIR)
            if VERBOSE:
                cached = result.get("cached", False)
                n = len(result.get("blocks", []))
                print(f"  [layout] {ark} → {n} blocs {'(cache)' if cached else '(paddle)'}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/iiif-info":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            if not ISSUE_ARK_FULL_RE.match(ark):
                self._send(400, "application/json", b'{"error":"ark invalide"}')
                return
            url = f"https://gallica.bnf.fr/iiif/ark:/12148/{ark}/f1/info.json"
            try:
                if HAS_CURL:
                    _, body = self._fetch_with_curl(url)
                else:
                    _, body = self._fetch_with_requests(url)
                info = json.loads(body)
            except Exception as e:
                self._send(500, "application/json",
                           json.dumps({"error": str(e)}).encode())
                return
            if not info.get("tiles"):
                info["tiles"] = [{"width": 512, "scaleFactors": [1, 2, 4, 8, 16, 32]}]
            if not info.get("profile") or len(info.get("profile", [])) < 2:
                info["profile"] = [
                    "http://iiif.io/api/image/2/level2.json",
                    {"qualities": ["native", "default"], "formats": ["jpg"]},
                ]
            elif isinstance(info["profile"][-1], dict):
                quals = info["profile"][-1].setdefault("qualities", [])
                if "native" not in quals:
                    quals.insert(0, "native")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(info, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/ocr-kraken-full":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            with OCR_SEMAPHORE:
                result = run_ocr_full_kraken(ark, OCR_CACHE_DIR, IMG_CACHE_DIR)
            if VERBOSE:
                cached = result.get("cached", False)
                print(f"  [kraken-full] {ark} → {result.get('n_lines', result.get('error','?'))} lignes "
                      f"{'(cache)' if cached else '(kraken)'}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/ocr-kraken-region":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            try:
                x0 = float((params.get("x0") or ["0"])[0])
                y0 = float((params.get("y0") or ["0"])[0])
                x1 = float((params.get("x1") or ["1"])[0])
                y1 = float((params.get("y1") or ["1"])[0])
            except ValueError:
                self._send(400, "application/json",
                           json.dumps({"error": "coords invalides"}).encode())
                return
            with OCR_SEMAPHORE:
                result = run_ocr_region_kraken(ark, x0, y0, x1, y1, IMG_CACHE_DIR)
            if VERBOSE:
                print(f"  [kraken-region] {ark} {x0:.2f},{y0:.2f}-{x1:.2f},{y1:.2f} → "
                      f"{result.get('n_lines', result.get('error', '?'))} lignes")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/meta":
            params = urllib.parse.parse_qs(parsed.query)
            ark = (params.get("ark") or [""])[0]
            result = self._get_meta(ark)
            if VERBOSE:
                print(f"  [meta] {ark} → {bool(result.get('description'))}")
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return

        # Endpoint pour voir le XML BRUT renvoyé par l'API Issues de Gallica.
        # Permet de diagnostiquer rapidement quels ARKs sont valides.
        # Exemple : /debug-raw?ark=cb34355551z&year=1936
        if parsed.path == "/debug-raw":
            params = urllib.parse.parse_qs(parsed.query)
            ark  = (params.get("ark") or ["cb34355551z"])[0]
            year = (params.get("year") or ["1936"])[0]
            api_url = (f"https://gallica.bnf.fr/services/Issues"
                       f"?ark=ark:/12148/{ark}/date&date={year}")
            try:
                if HAS_CURL:
                    _, body = self._fetch_with_curl(api_url)
                elif HAS_REQUESTS:
                    _, body = self._fetch_with_requests(api_url)
                else:
                    body = "Pas de backend HTTP disponible."
            except Exception as e:
                body = f"Erreur : {type(e).__name__}: {e}"

            # Compte les fascicules trouvés par le regex
            pattern = re.compile(
                rf'<issue\s+ark="({ISSUE_ARK_PATTERN})"[^>]*>\s*(\d{{4}})/(\d{{2}})/(\d{{2}})',
                re.IGNORECASE)
            matches = list(pattern.finditer(body))

            html = (f"<html><head><meta charset='utf-8'></head>"
                    f"<body style='font-family:monospace;padding:2em;background:#f4f0e8'>"
                    f"<h2>Debug API Issues — XML brut</h2>"
                    f"<p><b>ARK :</b> {ark}<br>"
                    f"<b>Année :</b> {year}<br>"
                    f"<b>URL appelée :</b> <a href='{api_url}' target='_blank'>{api_url}</a></p>"
                    f"<p><b>Fascicules trouvés par regex :</b> {len(matches)}</p>")
            if matches:
                html += "<ul>"
                for m in matches[:5]:
                    html += f"<li>{m.group(2)}/{m.group(3)}/{m.group(4)} → <code>{m.group(1)}</code></li>"
                if len(matches) > 5:
                    html += f"<li>... et {len(matches)-5} autres</li>"
                html += "</ul>"
            html += (f"<h3>XML brut (premiers 5000 caractères)</h3>"
                     f"<pre style='background:white;padding:1em;border:1px solid #ccc;"
                     f"white-space:pre-wrap;font-size:.85em'>"
                     f"{body[:5000].replace('<', '&lt;').replace('>', '&gt;')}"
                     f"</pre></body></html>")
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
            return

        # Endpoint de debug : ouvrir http://localhost:8765/debug?ark=cb34355551z&date=1936-05-25
        if parsed.path == "/debug":
            params = urllib.parse.parse_qs(parsed.query)
            ark  = (params.get("ark")  or ["cb34355551z"])[0]
            date = (params.get("date") or ["1936-05-25"])[0]
            result = self._resolve(ark, date)
            yyyymmdd = date.replace("-", "")
            test_url = f"https://gallica.bnf.fr/ark:/12148/{ark}/date{yyyymmdd}"
            html = (f"<html><body style='font-family:monospace;padding:2em'>"
                    f"<h2>Debug Gallica</h2>"
                    f"<p><b>Catalogue ARK :</b> {ark}<br>"
                    f"<b>Date :</b> {date}<br>"
                    f"<b>URL testée :</b> <a href='{test_url}' target='_blank'>{test_url}</a></p>"
                    f"<p><b>Résultat :</b></p>"
                    f"<pre>{json.dumps(result, indent=2)}</pre>"
                    f"</body></html>")
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
            return

        self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve(self, catalog_ark, iso_date):
        """Résout (titre, date) → ARK du fascicule en suivant la redirection Gallica.
        1 requête par (titre, date) — résultat mis en cache mémoire."""
        if not catalog_ark or not iso_date:
            return {"error": "missing parameters"}
        if not re.fullmatch(r"cb[a-z0-9]+", catalog_ark):
            return {"error": "invalid ark format"}
        try:
            Y, M, D = iso_date.split("-")
            assert len(Y) == 4 and len(M) == 2 and len(D) == 2
        except (ValueError, AssertionError):
            return {"error": "invalid date format"}

        cache_key = (catalog_ark, iso_date)
        with DATE_CACHE_LOCK:
            if cache_key in DATE_CACHE:
                cached = DATE_CACHE[cache_key]
                if VERBOSE:
                    print(f"    [cache] {catalog_ark} {iso_date} → {cached}")
                return {"issue_ark": cached}

        if _is_banned():
            remaining = int(_ban_until[0] - time.time())
            return {"error": "ip_banned", "retry_in": remaining}

        _acquire_token()

        with GALLICA_SEMAPHORE:
            with _request_lock:
                elapsed = time.time() - _last_request_time[0]
                if elapsed < MIN_DELAY_BETWEEN_REQUESTS:
                    time.sleep(MIN_DELAY_BETWEEN_REQUESTS - elapsed)
                _last_request_time[0] = time.time()
            try:
                issue_ark = self._resolve_by_redirect(catalog_ark, iso_date)
            except Exception as e:
                return {"error": f"upstream: {e}"}

        with DATE_CACHE_LOCK:
            DATE_CACHE[cache_key] = issue_ark

        if VERBOSE:
            print(f"    [redirect] {catalog_ark} {iso_date} → {issue_ark}")

        if issue_ark:
            return {"issue_ark": issue_ark}
        return {"issue_ark": None, "reason": "no_issue_on_date"}

    def _resolve_by_redirect(self, catalog_ark, iso_date):
        """Suit la redirection Gallica /date{YYYYMMDD} → URL finale.
        Si l'URL finale contient un ARK bpt6k, un numéro existe à cette date."""
        yyyymmdd = iso_date.replace("-", "")
        url = f"https://gallica.bnf.fr/ark:/12148/{catalog_ark}/date{yyyymmdd}"
        null_dev = "NUL" if sys.platform == "win32" else "/dev/null"

        if HAS_CURL:
            try:
                result = subprocess.run(
                    [CURL_PATH, "-sL", "--http1.1", "--max-time", "15",
                     "-w", "%{url_effective}",
                     "-o", null_dev,
                     "-A", USER_AGENT,
                     "-H", "Connection: close",
                     url],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="ignore", timeout=20,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError("timeout")
            if result.returncode != 0:
                if result.returncode == 35 and not _is_banned():
                    _trigger_ban()
                raise RuntimeError(f"curl exit={result.returncode}")
            final_url = result.stdout.strip()
        elif HAS_REQUESTS:
            r = requests.get(url, allow_redirects=True, timeout=15,
                             headers={"User-Agent": USER_AGENT, "Connection": "close"})
            final_url = r.url
        else:
            raise RuntimeError("pas de backend HTTP")

        m = re.search(rf"/ark:/12148/({ISSUE_ARK_PATTERN})", final_url, re.IGNORECASE)
        return m.group(1) if m else None

    def _get_year_issues(self, catalog_ark, year):
        """Récupère le dictionnaire {iso_date: bpt6k_ark} pour un titre/année.
        Utilise le cache mémoire (et disque), sinon appelle l'API Issues."""
        cache_key = (catalog_ark, year)
        with CACHE_LOCK:
            if cache_key in YEAR_CACHE:
                if VERBOSE:
                    print(f"    [cache] hit {catalog_ark} {year} ({len(YEAR_CACHE[cache_key])} numéros)")
                return YEAR_CACHE[cache_key]

        if _is_banned():
            remaining = int(_ban_until[0] - time.time())
            return {"error": "ip_banned", "retry_in": remaining}

        _acquire_token()

        url = (f"https://gallica.bnf.fr/services/Issues"
               f"?ark=ark:/12148/{catalog_ark}/date&date={year}")

        with GALLICA_SEMAPHORE:
            with _request_lock:
                elapsed = time.time() - _last_request_time[0]
                if elapsed < MIN_DELAY_BETWEEN_REQUESTS:
                    time.sleep(MIN_DELAY_BETWEEN_REQUESTS - elapsed)
                _last_request_time[0] = time.time()
            try:
                if HAS_CURL:
                    _, body = self._fetch_with_curl(url)
                elif HAS_REQUESTS:
                    _, body = self._fetch_with_requests(url)
                else:
                    return {"error": "ni curl ni requests"}
            except Exception as e:
                return {"error": f"upstream: {type(e).__name__}: {e}"}

        # Parse le XML : <issue ark="bpt6k... | bd6t... | btv1b..." ...>YYYY/MM/DD ...</issue>
        issues = {}
        pattern = re.compile(
            rf'<issue\s+ark="({ISSUE_ARK_PATTERN})"[^>]*>\s*(\d{{4}})/(\d{{2}})/(\d{{2}})',
            re.IGNORECASE
        )
        for m in pattern.finditer(body):
            ark, y, mo, d = m.group(1), m.group(2), m.group(3), m.group(4)
            issues[f"{y}-{mo}-{d}"] = ark

        if VERBOSE:
            print(f"    [API Issues] {catalog_ark} {year} → {len(issues)} numéros")

        with CACHE_LOCK:
            YEAR_CACHE[cache_key] = issues
        return issues

    def _fetch_with_curl(self, url):
        """Récupère le corps de la réponse via curl, avec retry sur erreurs transitoires.
        L'API Issues étant légère et documentée, les erreurs sont rares."""
        transient_codes = {7, 28, 35, 56, 18}
        max_attempts = 3
        last_err = "unknown"

        for attempt in range(1, max_attempts + 1):
            try:
                result = subprocess.run(
                    [CURL_PATH,
                     "-sL",
                     "--http1.1",
                     "--max-time", "20",
                     "--retry", "0",
                     "-A", USER_AGENT,
                     "-H", "Accept: application/xml, text/xml, */*",
                     "-H", "Accept-Language: fr-FR,fr;q=0.9",
                     "-H", "Connection: close",
                     url],
                    capture_output=True, text=True, timeout=25,
                    encoding="utf-8", errors="ignore",
                )
            except subprocess.TimeoutExpired:
                last_err = "subprocess timeout"
                time.sleep(2 * attempt)
                continue

            if result.returncode == 0:
                return url, (result.stdout or "")

            last_err = f"curl exit={result.returncode}"
            if result.returncode in transient_codes and attempt < max_attempts:
                delay = 2.0 * attempt
                if VERBOSE:
                    print(f"    [retry {attempt}/{max_attempts}] {last_err} — pause {delay:.1f}s")
                time.sleep(delay)
                continue
            break

        # exit=35 après toutes les tentatives = ban IP confirmé → circuit breaker
        if "exit=35" in last_err and not _is_banned():
            _trigger_ban()
        raise RuntimeError(last_err)

    def _fetch_with_requests(self, url):
        """Fallback requests (Session créée à la volée, pas de keep-alive entre threads)."""
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9",
            "Connection": "close",  # évite le keep-alive (cause probable du 10054)
        }
        r = requests.get(url, timeout=20, allow_redirects=True, headers=headers)
        return r.url, r.text[:50000] if r.text else ""

    def _get_ocr(self, issue_ark):
        if not ISSUE_ARK_FULL_RE.match(issue_ark):
            return {"error": "invalid ark format"}
        cache_file = OCR_CACHE_DIR / f"{issue_ark}.txt"
        if cache_file.exists():
            text = cache_file.read_text(encoding="utf-8", errors="replace")
            return {"text": text, "cached": True, "length": len(text)}
        url = f"https://gallica.bnf.fr/ark:/12148/{issue_ark}/f1.texteBrut"
        try:
            _, body = self._fetch_content(url)
        except Exception as e:
            return {"error": str(e)}
        # Gallica retourne une page HTML 404 si l'OCR est absent
        if body.lstrip().startswith("<!") or "<html" in body[:200].lower():
            return {"error": "OCR indisponible pour ce numéro"}
        cache_file.write_text(body, encoding="utf-8")
        return {"text": body, "cached": False, "length": len(body)}

    def _get_meta(self, issue_ark):
        if not ISSUE_ARK_FULL_RE.match(issue_ark):
            return {"error": "invalid ark format"}
        cache_file = META_CACHE_DIR / f"{issue_ark}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            data["cached"] = True
            return data
        url = (f"http://oai.bnf.fr/oai2/OAIHandler"
               f"?verb=GetRecord&metadataPrefix=oai_dc"
               f"&identifier=oai:bnf.fr:gallica/ark:/12148/{issue_ark}")
        try:
            _, xml = self._fetch_content(url)
        except Exception as e:
            return {"error": str(e)}
        meta = self._parse_dc(xml)
        cache_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        meta["cached"] = False
        return meta

    def _parse_dc(self, xml):
        def first(tag):
            m = re.search(rf"<dc:{tag}[^>]*>([^<]*)</dc:{tag}>", xml, re.IGNORECASE)
            return m.group(1).strip() if m else ""
        descriptions = re.findall(r"<dc:description[^>]*>([^<]+)</dc:description>", xml, re.IGNORECASE)
        desc = " ".join(d.strip() for d in descriptions if d.strip())
        return {
            "title":       first("title"),
            "date":        first("date"),
            "publisher":   first("publisher"),
            "description": desc,
            "identifier":  first("identifier"),
        }

    def _fetch_content(self, url):
        """Requête de contenu (OCR, DC) — semaphore dédié, délai minimum 2s."""
        if _is_banned():
            remaining = int(_ban_until[0] - time.time())
            raise RuntimeError(f"ip_banned:{remaining}")
        _acquire_token()
        with OCR_SEMAPHORE:
            with _content_lock:
                elapsed = time.time() - _last_content_time[0]
                wait = MIN_DELAY_CONTENT - elapsed
                if wait > 0:
                    time.sleep(wait)
                _last_content_time[0] = time.time()
            if HAS_CURL:
                return self._fetch_with_curl(url)
            elif HAS_REQUESTS:
                return self._fetch_with_requests(url)
            else:
                raise RuntimeError("Pas de backend HTTP disponible")

    # silencer les logs HTTP (commenter pour debug)
    def log_message(self, fmt, *args):
        pass


# =============================================================================
# THREADED HTTP SERVER (sinon, requêtes séquentielles → lent)
# =============================================================================
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def find_free_port(start=DEFAULT_PORT, attempts=10):
    for p in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def main():
    OCR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    META_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    port = find_free_port()
    if port is None:
        print("❌ Aucun port libre trouvé entre 8765 et 8774.", file=sys.stderr)
        sys.exit(1)

    server = ThreadedHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://localhost:{port}"
    if HAS_CURL:
        backend = f"curl ({CURL_PATH})"
    elif HAS_REQUESTS:
        backend = "requests (fallback — curl introuvable)"
    else:
        backend = "AUCUN — installe curl ou requests"
    print(f"🚀 Navigateur Gallica en marche")
    print(f"   URL    : {url}")
    print(f"   HTTP   : {backend}")
    print(f"   API    : Issues (officielle) — 1 requête / titre / année, mise en cache mémoire")
    print(f"   Debug  : {url}/debug?ark=cb34355551z&date=1936-05-25")
    print(f"   XML brut: {url}/debug-raw?ark=cb34355551z&year=1936")
    print(f"   Cache  : {url}/api/cache  (ajouter ?clear=1 pour vider)")
    print(f"   Verbose: relancer avec   python {sys.argv[0]} --verbose")
    print(f"   Stop   : Ctrl+C")

    # Ouvre automatiquement le navigateur (après 0.8s pour laisser le serveur prêt)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Arrêt du serveur.")


if __name__ == "__main__":
    main()
