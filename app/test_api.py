#!/usr/bin/env python3
"""
Test de l'API Issues de Gallica — avec concurrence contrôlée.

Usage :
    python test_api.py              # teste tous les ARKs de NEWSPAPERS
    python test_api.py --workers 3  # 3 requêtes simultanées max (défaut : 2)
    python test_api.py --year 1936  # année à tester (défaut : 1930)
"""
import subprocess
import sys
import threading
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://gallica.bnf.fr/services/Issues?ark=ark:/12148/{ark}/date&date={year}"

# Même liste que dans gallica_unes_server.py — à synchroniser si besoin
NEWSPAPERS = [
    ("Le Figaro",             "cb34355551z"),
    ("Le Petit Journal",      "cb32895690j"),
    ("Le Petit Parisien",     "cb34419111x"),
    ("Le Matin",              "cb328123058"),
    ("L'Humanité",            "cb327877302"),
    ("La Croix",              "cb343631418"),
    ("Le Journal",            "cb34473289x"),
    ("L'Écho de Paris",       "cb34429768r"),
    ("Le Gaulois",            "cb32779904b"),
    ("L'Intransigeant",       "cb32793876w"),
    ("Excelsior",             "cb32771891w"),
    ("L'Action française",    "cb326819451"),
    ("L'Œuvre",               "cb34429265b"),
    ("Journal des débats",    "cb39294634r"),
]

lock = print_lock = threading.Lock()


def fetch_issues(name: str, ark: str, year: int) -> dict:
    url = BASE_URL.format(ark=ark, year=year)
    result = subprocess.run(
        ["curl", "-sL", "--max-time", "20", "--http1.1",
         "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
         url],
        capture_output=True
    )
    ok = result.returncode == 0 and b"<issue " in result.stdout
    count = result.stdout.count(b"<issue ") if ok else 0
    error = result.stderr.decode("ascii", errors="replace").strip() if result.returncode != 0 else ""
    return {
        "name": name,
        "ark": ark,
        "ok": ok,
        "count": count,
        "exit": result.returncode,
        "error": error,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2,
                        help="Requêtes simultanées max (défaut : 2)")
    parser.add_argument("--year", type=int, default=1930,
                        help="Année à tester (défaut : 1930)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Délai (s) entre chaque requête dans un worker (défaut : 1.5)")
    args = parser.parse_args()

    print(f"Test API Issues Gallica — année {args.year}, {args.workers} worker(s), délai {args.delay}s\n")
    print(f"{'Titre':<25} {'ARK':<16} {'Résultat'}")
    print("-" * 60)

    results = []
    batches = [NEWSPAPERS[i:i + args.workers] for i in range(0, len(NEWSPAPERS), args.workers)]
    for b, batch in enumerate(batches):
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            wave = {executor.submit(fetch_issues, name, ark, args.year): (name, ark)
                    for name, ark in batch}
            for future in as_completed(wave):
                r = future.result()
                results.append(r)
                status = f"OK  {r['count']} numeros" if r["ok"] else f"NOK exit={r['exit']} -- {r['error'] or 'aucun numero trouve'}"
                with print_lock:
                    print(f"{r['name']:<25} {r['ark']:<16} {status}")
        if b < len(batches) - 1:
            print(f"  [pause {args.delay}s avant prochaine vague]")
            time.sleep(args.delay)

    ok = sum(1 for r in results if r["ok"])
    print(f"\n{ok}/{len(results)} titres résolus.")
    if ok < len(results):
        print("\nARKs à corriger :")
        for r in results:
            if not r["ok"]:
                print(f"  - {r['name']} : {r['ark']}")


if __name__ == "__main__":
    main()
