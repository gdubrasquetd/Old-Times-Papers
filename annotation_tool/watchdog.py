"""
Watchdog : maintient server.py vivant ET réactif.

Détecte deux cas de panne :
  - process terminé (crash dur),
  - process vivant mais qui ne répond plus sur le port (zombie — exactement le
    cas qui passait inaperçu jusqu'ici).

Sur panne confirmée, tue le process et le relance, en journalisant chaque
incident horodaté dans data/watchdog.log (et sur la sortie standard).

Lancer ça AU LIEU de server.py :
    python watchdog.py
"""
from __future__ import annotations
import subprocess, sys, time, datetime, pathlib, urllib.request

# Console Windows = cp1252 par défaut : sans ça, un print accentué (ou un '→')
# lève UnicodeEncodeError et tue le watchdog au pire moment.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT   = pathlib.Path(__file__).resolve().parent
PY     = sys.executable
LOGF   = ROOT / "data" / "watchdog.log"
HEALTH = "http://localhost:5050/api/health"
CHECK_EVERY = 10      # secondes entre deux checks de santé
FAIL_LIMIT  = 2       # checks ratés consécutifs avant de conclure au crash


def log(msg: str) -> None:
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def healthy(timeout: float = 4) -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start() -> subprocess.Popen:
    p = subprocess.Popen([PY, "server.py"], cwd=str(ROOT))
    log(f"server.py démarré (PID {p.pid})")
    return p


def wait_healthy(deadline: float = 30) -> bool:
    t0 = time.time()
    while time.time() - t0 < deadline:
        if healthy():
            return True
        time.sleep(1)
    return False


def main() -> None:
    log("=== watchdog démarré ===")
    proc = start()
    log("serveur opérationnel (health OK)" if wait_healthy()
        else "ATTENTION : serveur pas opérationnel au démarrage")

    fails = 0
    while True:
        time.sleep(CHECK_EVERY)
        dead = proc.poll() is not None
        ok = (not dead) and healthy()
        if ok:
            fails = 0
            continue
        fails += 1
        reason = "process terminé (crash)" if dead else "ne répond plus (zombie)"
        log(f"DÉTECTION : {reason} — échec {fails}/{FAIL_LIMIT}")
        if fails >= FAIL_LIMIT:
            log(f"CRASH confirmé ({reason}) -> redémarrage")
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception as e:
                log(f"  kill échoué : {e}")
            proc = start()
            wait_healthy()
            fails = 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("=== watchdog arrêté (Ctrl+C) ===")
