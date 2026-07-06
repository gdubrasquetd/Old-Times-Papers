#!/usr/bin/env python3
"""
test_connection.py — Diagnostique pourquoi Python n'arrive pas à joindre Gallica.

Lance différents tests et affiche un verdict.

Usage :
    python test_connection.py
"""
import sys
import socket
import ssl
import subprocess
import platform

GALLICA_HOST = "gallica.bnf.fr"
TEST_URL     = "https://gallica.bnf.fr/ark:/12148/cb34355551z/date19360525"
CTRL_URL     = "https://httpbin.org/get"

# --------------------------------------------------------------------------
# Helpers d'affichage
# --------------------------------------------------------------------------
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"
BOLD  = "\033[1m";  RESET = "\033[0m"

def title(s):  print(f"\n{BOLD}━━━ {s} ━━━{RESET}")
def ok(s):     print(f"  {GREEN}✓{RESET} {s}")
def fail(s):   print(f"  {RED}✗{RESET} {s}")
def warn(s):   print(f"  {YELLOW}!{RESET} {s}")
def info(s):   print(f"    {s}")

# Active les codes couleur ANSI sur Windows
if platform.system() == "Windows":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        GREEN = RED = YELLOW = BOLD = RESET = ""

# --------------------------------------------------------------------------
# 1) Versions et environnement
# --------------------------------------------------------------------------
title("Environnement")
info(f"Python  : {sys.version.split()[0]} ({platform.system()} {platform.release()})")
info(f"OpenSSL : {ssl.OPENSSL_VERSION}")
try:
    import requests, urllib3
    info(f"requests: {requests.__version__}, urllib3: {urllib3.__version__}")
    HAS_REQUESTS = True
except ImportError:
    warn("requests non installé")
    HAS_REQUESTS = False

# --------------------------------------------------------------------------
# 2) Résolution DNS
# --------------------------------------------------------------------------
title("Résolution DNS")
try:
    ip = socket.gethostbyname(GALLICA_HOST)
    ok(f"{GALLICA_HOST} → {ip}")
except Exception as e:
    fail(f"DNS échoue : {e}")
    info("→ Problème réseau de base. Vérifie ta connexion internet.")
    sys.exit(1)

# --------------------------------------------------------------------------
# 3) Connexion TCP brute (port 443)
# --------------------------------------------------------------------------
title("Connexion TCP port 443")
try:
    with socket.create_connection((GALLICA_HOST, 443), timeout=10) as s:
        ok(f"TCP {GALLICA_HOST}:443 ouvert")
except Exception as e:
    fail(f"TCP bloqué : {e}")
    info("→ Pare-feu ou réseau bloque la sortie sur le port 443.")
    sys.exit(1)

# --------------------------------------------------------------------------
# 4) Handshake TLS
# --------------------------------------------------------------------------
title("Handshake TLS")
try:
    ctx = ssl.create_default_context()
    with socket.create_connection((GALLICA_HOST, 443), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=GALLICA_HOST) as ssock:
            cert = ssock.getpeercert()
            issuer = dict(x[0] for x in cert.get("issuer", []))
            subject = dict(x[0] for x in cert.get("subject", []))
            ok(f"TLS {ssock.version()} négocié")
            info(f"Cert sujet  : {subject.get('commonName', '?')}")
            info(f"Cert émetteur: {issuer.get('commonName', '?')}")
            # Détection SSL interception : si l'émetteur n'est pas une CA publique connue
            issuer_cn = issuer.get('commonName', '').lower()
            suspicious = any(av in issuer_cn for av in
                             ['avast', 'kaspersky', 'eset', 'bitdefender',
                              'norton', 'mcafee', 'avira', 'sophos', 'trend',
                              'webroot', 'comodo internet', 'family safety'])
            if suspicious:
                warn(f"⚠️ L'émetteur ressemble à un antivirus qui intercepte HTTPS !")
                warn(f"   C'est très probablement la cause des connexions cassées.")
            elif issuer_cn and not any(x in issuer_cn for x in
                                       ['certigna', 'digicert', 'globalsign',
                                        "let's encrypt", 'sectigo', 'rapidssl',
                                        'thawte', 'verisign', 'gandi', 'amazon']):
                warn(f"⚠️ Émetteur de certificat inhabituel : {issuer_cn}")
                warn(f"   Possible interception SSL par un logiciel local.")
except Exception as e:
    fail(f"TLS échoue : {type(e).__name__}: {e}")
    info("→ Problème de handshake TLS. Possible interception SSL.")

# --------------------------------------------------------------------------
# 5) Test requests vers httpbin (témoin)
# --------------------------------------------------------------------------
if HAS_REQUESTS:
    title("requests vers httpbin.org (témoin)")
    try:
        r = requests.get(CTRL_URL, timeout=10)
        ok(f"HTTP {r.status_code} — requests fonctionne pour d'autres sites")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
        warn("requests ne marche même pas vers httpbin → problème général de réseau Python")

    # --------------------------------------------------------------------------
    # 6) Test requests vers Gallica racine
    # --------------------------------------------------------------------------
    title("requests vers Gallica (racine)")
    try:
        r = requests.get(f"https://{GALLICA_HOST}/", timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
        })
        ok(f"HTTP {r.status_code} — racine accessible")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")

    # --------------------------------------------------------------------------
    # 7) Test requests vers une URL ARK Gallica
    # --------------------------------------------------------------------------
    title("requests vers une URL ARK Gallica")
    try:
        r = requests.get(TEST_URL, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
        })
        ok(f"HTTP {r.status_code} — URL finale : {r.url}")
        if "bpt6k" in r.url:
            ok(f"  ARK fascicule trouvé dans l'URL")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")

    # --------------------------------------------------------------------------
    # 8) Avec verify=False (test si c'est un problème de certificat)
    # --------------------------------------------------------------------------
    title("requests vers Gallica avec verify=False (test certif)")
    try:
        import warnings
        warnings.filterwarnings('ignore')
        r = requests.get(TEST_URL, timeout=15, verify=False, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
        })
        ok(f"HTTP {r.status_code} — URL finale : {r.url}")
        warn("⚠️ Marche sans vérif cert → confirme probable interception SSL.")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")

# --------------------------------------------------------------------------
# 9) Test curl (utilise sa propre stack TLS, souvent contournement antivirus)
# --------------------------------------------------------------------------
title("curl vers Gallica")
try:
    result = subprocess.run(
        ["curl", "-sIL", "-o", "NUL" if platform.system() == "Windows" else "/dev/null",
         "-w", "%{http_code} %{url_effective}", "--max-time", "15",
         "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
         TEST_URL],
        capture_output=True, text=True, timeout=20
    )
    if result.returncode == 0:
        ok(f"curl OK → {result.stdout.strip()}")
        if "bpt6k" in result.stdout:
            ok("ARK fascicule trouvé via curl !")
            print()
            print(f"{BOLD}{GREEN}👉 SOLUTION : curl fonctionne là où requests échoue.")
            print(f"   On peut basculer le serveur sur curl en backend.{RESET}")
    else:
        fail(f"curl returncode={result.returncode}")
        info(f"stderr: {result.stderr.strip()[:200]}")
except FileNotFoundError:
    warn("curl non installé sur cette machine")
except Exception as e:
    fail(f"{type(e).__name__}: {e}")

# --------------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------------
print(f"\n{BOLD}━━━ Verdict ━━━{RESET}")
print("Envoie-moi la sortie complète de ce script pour qu'on adapte le code.")
print("Indices typiques :")
print(f"  • Si l'émetteur du certificat est un antivirus → désactive l'inspection HTTPS")
print(f"    pour gallica.bnf.fr dans les paramètres de l'antivirus, OU on bascule sur curl.")
print(f"  • Si curl marche mais pas requests → on bascule sur curl en backend.")
print(f"  • Si rien ne marche → réseau ou pare-feu bloque Gallica spécifiquement.")
