"""
Telechargeur de masse pour les unes de journaux Gallica.

Pour chaque (journal, date), resout l'ARK fascicule via l'API Issues puis
telecharge la une en pleine resolution (IIIF). Enregistre dans la DB et
sur le disque.

Usage :
  python downloader.py --year 1930              # toutes les unes de 1930
  python downloader.py --year 1930 --month 5    # mai 1930
  python downloader.py --range 1900-1939        # plusieurs annees
  python downloader.py --date 1930-05-25 --journals figaro humanite
  python downloader.py --target 500             # arrete a 500 unes telechargees
"""
from __future__ import annotations
import sys, re, pathlib, time, subprocess, shutil, argparse, random
try:  # console Windows cp1252 ; stdout peut ne pas exposer reconfigure (ex: capture pytest)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from PIL import Image
import db

ROOT     = pathlib.Path(__file__).parent
IMG_DIR  = ROOT / "data" / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "Mozilla/5.0 (compatible; OldsPapers-annot/1.0)"
CURL = shutil.which("curl") or "curl"

# Tous les journaux nationaux tier 1 (synchro avec gallica_unes_server.py)
JOURNAUX = [
    ("action_francaise",   "L'Action francaise",  "cb326819451", 1908, 1944),
    ("la_croix",           "La Croix",            "cb343631418", 1880, 1944),
    ("echo_de_paris",      "L'Echo de Paris",     "cb34429768r", 1884, 1944),
    ("excelsior",          "Excelsior",           "cb32771891w", 1910, 1940),
    ("le_figaro",          "Le Figaro",           "cb34355551z", 1854, 1955),
    ("le_gaulois",         "Le Gaulois",          "cb32779904b", 1868, 1929),
    ("humanite",           "L'Humanite",          "cb327877302", 1904, 1944),
    ("intransigeant",      "L'Intransigeant",     "cb32793876w", 1880, 1944),
    ("le_journal",         "Le Journal",          "cb34473289x", 1892, 1944),
    ("journal_des_debats", "Journal des debats",  "cb39294634r", 1814, 1944),
    ("le_matin",           "Le Matin",            "cb328123058", 1884, 1944),
    ("oeuvre",             "L'Oeuvre",            "cb34429265b", 1904, 1944),
    ("paris_soir",         "Paris-Soir",          "cb34431897x", 1923, 1944),
    ("petit_journal",      "Le Petit Journal",    "cb32895690j", 1863, 1944),
    ("petit_parisien",     "Le Petit Parisien",   "cb34419111x", 1876, 1944),
    ("populaire",          "Le Populaire",        "cb34393339w", 1916, 1944),
    ("le_temps",           "Le Temps",            "cb34431794k", 1861, 1942),
]


def fetch_text(url: str, timeout: int = 30) -> str:
    r = subprocess.run(
        [CURL, "-sL", "--http1.1", "--max-time", str(timeout),
         "-A", USER_AGENT,
         "-H", "Accept: application/xml, text/xml, */*",
         url],
        capture_output=True, text=True, timeout=timeout + 10,
        encoding="utf-8", errors="ignore",
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl exit={r.returncode}")
    return r.stdout or ""


def fetch_binary(url: str, dest: pathlib.Path, timeout: int = 180):
    r = subprocess.run(
        [CURL, "-sL", "--http1.1", "--max-time", str(timeout),
         "-A", USER_AGENT,
         "-o", str(dest), url],
        capture_output=True, text=True, timeout=timeout + 20,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl exit={r.returncode}")
    if not dest.exists() or dest.stat().st_size < 50_000:
        raise RuntimeError(f"fichier trop petit ({dest.stat().st_size if dest.exists() else 0})")


def fetch_year_issues(catalog_ark: str, year: int) -> dict[int, str]:
    """Retourne {dayOfYear: issue_ark} pour cette annee."""
    url = f"https://gallica.bnf.fr/services/Issues?ark=ark:/12148/{catalog_ark}/date&date={year}"
    body = fetch_text(url)
    issues = {}
    pat = re.compile(r'<issue\s+ark="(bpt6k[a-z0-9]+)"\s+dayOfYear="(\d+)"', re.I)
    for m in pat.finditer(body):
        issues[int(m.group(2))] = m.group(1)
    return issues


def download_one(slug_journal: str, journal_titre: str, catalog_ark: str,
                  iso_date: str, issue_ark: str) -> bool:
    """Telecharge une une et l'enregistre en DB. Retourne True si succes."""
    img_slug = f"{slug_journal}_{iso_date}"
    img_path = IMG_DIR / f"{img_slug}.jpg"

    if img_path.exists() and img_path.stat().st_size > 50_000:
        # Deja telecharge, juste s'assurer qu'il est en DB
        try:
            w, h = Image.open(img_path).size
            db.add_image(img_slug, journal_titre, iso_date, catalog_ark,
                         issue_ark, str(img_path), w, h)
            return True
        except Exception:
            img_path.unlink()

    img_url = f"https://gallica.bnf.fr/iiif/ark:/12148/{issue_ark}/f1/full/full/0/native.jpg"
    try:
        fetch_binary(img_url, img_path)
        w, h = Image.open(img_path).size
        db.add_image(img_slug, journal_titre, iso_date, catalog_ark,
                     issue_ark, str(img_path), w, h)
        return True
    except Exception as e:
        print(f"    ERR : {e}", flush=True)
        if img_path.exists():
            img_path.unlink()
        return False


def iter_dates(year: int, month: int | None = None) -> list[str]:
    """Liste les dates iso de l'annee (ou du mois si specifie)."""
    from datetime import date, timedelta
    if month:
        d0 = date(year, month, 1)
        d1 = date(year + (month == 12), (month % 12) + 1, 1) if month < 12 else date(year + 1, 1, 1)
    else:
        d0 = date(year, 1, 1)
        d1 = date(year + 1, 1, 1)
    out = []
    d = d0
    while d < d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def download_batch(n: int = 20,
                    year_range: tuple[int, int] = (1900, 1939),
                    delay: float = 1.5,
                    shuffle: bool = True,
                    balance: bool = True,
                    log=print) -> int:
    """Telecharge jusqu'a n nouvelles unes (skip les slugs deja en DB).
    Pour appel depuis le serveur (replenisseur de fond).

    Si balance=True, priorise les journaux SOUS-REPRESENTES dans les unes deja
    annotees (status='done') : a chaque tour on sert le journal dont le compte
    'done' courant est le plus faible (round-robin pondere), de sorte que le pool
    todo se remplisse vers un jeu d'entrainement plus equilibre entre titres.

    Retourne le nombre de unes effectivement telechargees."""
    from datetime import date as _date, timedelta as _td
    db.init_db()
    existing = db.existing_slugs()

    # Representation courante par titre = nb d'unes deja annotees (done).
    done_counts = db.count_by_journal(status="done") if balance else {}

    # Pour chaque journal valide sur la plage : une file d'annees a explorer.
    jstate: dict[str, dict] = {}
    for slug, titre, ark, y_start, y_end in JOURNAUX:
        years = [y for y in range(year_range[0], year_range[1] + 1)
                 if y_start <= y <= y_end]
        if not years:
            continue
        if shuffle:
            random.shuffle(years)
        jstate[slug] = {"titre": titre, "ark": ark, "years": years,
                        "yi": 0, "rep": done_counts.get(titre, 0), "fails": 0}

    n_ok = 0
    year_cache: dict[tuple[str, int], dict[int, str]] = {}
    tried: set[str] = set()          # slugs_date deja tentes (echec) ce batch
    active = set(jstate.keys())

    while n_ok < n and active:
        # Journal le moins represente (bruit pour casser les egalites a l'identique).
        slug = (min(active, key=lambda s: (jstate[s]["rep"], random.random()))
                if balance else random.choice(list(active)))
        st = jstate[slug]
        titre, ark = st["titre"], st["ark"]

        # Avancer dans ses annees jusqu'a trouver un jour telechargeable.
        picked = None
        while st["yi"] < len(st["years"]):
            year = st["years"][st["yi"]]
            key = (ark, year)
            if key not in year_cache:
                try:
                    year_cache[key] = fetch_year_issues(ark, year)
                    time.sleep(delay)
                except Exception:
                    year_cache[key] = {}
            doys = list(year_cache[key].keys())
            if shuffle:
                random.shuffle(doys)
            for doy in doys:
                iso_date = (_date(year, 1, 1) + _td(days=doy - 1)).isoformat()
                img_slug = f"{slug}_{iso_date}"
                if img_slug in existing or img_slug in tried:
                    continue
                picked = (year, doy, iso_date, img_slug, year_cache[key][doy])
                break
            if picked:
                break
            st["yi"] += 1            # annee epuisee -> suivante

        if not picked:
            active.discard(slug)     # ce journal n'a plus rien a offrir
            continue

        year, doy, iso_date, img_slug, issue_ark = picked
        log(f"  [replenish] {img_slug} -> {issue_ark} (done={st['rep']})")
        if download_one(slug, titre, ark, iso_date, issue_ark):
            n_ok += 1
            existing.add(img_slug)
            st["rep"] += 1           # rebalance : ce titre est moins prioritaire
            st["fails"] = 0
        else:
            tried.add(img_slug)      # ne pas re-tenter ce jour
            st["fails"] += 1
            if st["fails"] >= 3:     # journal qui echoue en boucle -> on l'ecarte
                active.discard(slug)
        time.sleep(delay)
    return n_ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, help="Annee (ex: 1930)")
    parser.add_argument("--month", type=int, help="Mois (1-12)")
    parser.add_argument("--range", help="Range d'annees, ex: 1900-1939")
    parser.add_argument("--date", help="Une seule date ISO YYYY-MM-DD")
    parser.add_argument("--journals", nargs="*",
                        help=f"Slugs (defaut: tous). Choix: {[j[0] for j in JOURNAUX]}")
    parser.add_argument("--target", type=int, default=None,
                        help="Arret apres N unes telechargees (anti rate-limit)")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Pause entre requetes (secondes)")
    parser.add_argument("--shuffle", action="store_true",
                        help="Melange l'ordre (utile pour distribuer la charge)")
    args = parser.parse_args()

    db.init_db()

    # 1. Quels journaux ?
    if args.journals:
        journaux = [j for j in JOURNAUX if j[0] in args.journals]
    else:
        journaux = JOURNAUX

    # 2. Quelles dates ?
    if args.date:
        years_dates = [(int(args.date[:4]), [args.date])]
    elif args.range:
        a, b = args.range.split("-")
        years_dates = [(y, iter_dates(y)) for y in range(int(a), int(b) + 1)]
    elif args.year:
        years_dates = [(args.year, iter_dates(args.year, args.month))]
    else:
        # Defaut : 1930
        years_dates = [(1930, iter_dates(1930))]

    n_ok = n_skip = n_err = 0
    target = args.target or 99_999_999

    # Construire la liste totale (journal, year, iso_date)
    tasks = []
    for year, dates in years_dates:
        for slug, titre, ark, y_start, y_end in journaux:
            if not (y_start <= year <= y_end):
                continue
            for iso_date in dates:
                tasks.append((slug, titre, ark, year, iso_date))

    if args.shuffle:
        random.shuffle(tasks)

    print(f"=== {len(tasks)} (journal, date) potentiels (target={target}) ===\n",
          flush=True)

    # Cache des "issues par annee" pour eviter de re-demander l'API
    year_cache: dict[tuple[str, int], dict[int, str]] = {}

    for i, (slug, titre, ark, year, iso_date) in enumerate(tasks):
        if n_ok >= target:
            print(f"\nTarget {target} atteint, arret.")
            break

        # Resoudre l'ARK fascicule via le cache d'annee
        key = (ark, year)
        if key not in year_cache:
            try:
                year_cache[key] = fetch_year_issues(ark, year)
                time.sleep(args.delay)
            except Exception as e:
                print(f"  [{i+1}/{len(tasks)}] {slug} {iso_date} : ERR Issues {e}",
                      flush=True)
                year_cache[key] = {}
                continue

        issues = year_cache[key]
        if not issues:
            n_skip += 1
            continue

        from datetime import date as _date
        try:
            doy = _date.fromisoformat(iso_date).timetuple().tm_yday
        except Exception:
            n_skip += 1
            continue

        if doy not in issues:
            n_skip += 1
            continue

        issue_ark = issues[doy]
        print(f"  [{i+1}/{len(tasks)}] {slug} {iso_date} -> {issue_ark}",
              end=" ", flush=True)
        ok = download_one(slug, titre, ark, iso_date, issue_ark)
        if ok:
            n_ok += 1
            print(f"OK  (total OK: {n_ok})", flush=True)
        else:
            n_err += 1
        time.sleep(args.delay)

    print(f"\n=== Bilan : OK={n_ok}  skip={n_skip}  err={n_err} ===")
    s = db.stats()
    print(f"En DB : {s['total']} unes ({s['done']} done, {s['in_progress']} in_progress, {s['todo']} todo)")


if __name__ == "__main__":
    main()
