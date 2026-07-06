"""SQLite helpers pour l'outil d'annotation."""
from __future__ import annotations
import sqlite3, json, pathlib, time
from typing import Iterable

ROOT = pathlib.Path(__file__).parent
DB_PATH = ROOT / "data" / "annotations.db"

DEFAULT_LABELS = [
    # (name, color hex, description)
    ("header",         "#f15bb5", "Bandeau / en-tete (titre journal, date, edition, infos pratiques)"),
    ("titre",          "#e76f51", "Titre, sous-titre, titre d'article ou de section"),
    ("illustration",   "#2a9d8f", "Photo, gravure, dessin, vignette"),
    ("bloc de texte",  "#e9c46a", "Corps d'article / paragraphes multiples formant un bloc"),
    ("texte isolé",    "#f4a261", "Texte isole : slogan, accroche, ligne unique, encart bref"),
    ("autres",         "#888888", "Pub, tableau, filet, legende, pied de page, etc."),
]

# Migrations : (ancien_nom, nouveau_nom). Renommage idempotent qui preserve
# les annotations existantes (le label_id reste inchange).
LABEL_RENAMES = [
    ("texte", "bloc de texte"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT UNIQUE NOT NULL,
    journal      TEXT NOT NULL,
    iso_date     TEXT NOT NULL,
    catalog_ark  TEXT NOT NULL,
    issue_ark    TEXT,
    path         TEXT NOT NULL,
    w            INTEGER,
    h            INTEGER,
    downloaded_at INTEGER NOT NULL,
    status       TEXT DEFAULT 'todo'      -- todo / in_progress / done / skipped
);

CREATE INDEX IF NOT EXISTS idx_images_status  ON images(status);
CREATE INDEX IF NOT EXISTS idx_images_journal ON images(journal);

CREATE TABLE IF NOT EXISTS labels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    color       TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS annotations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id   INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    label_id   INTEGER NOT NULL REFERENCES labels(id),
    x0         INTEGER NOT NULL,
    y0         INTEGER NOT NULL,
    x1         INTEGER NOT NULL,
    y1         INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anno_image ON annotations(image_id);

-- Suggestions de boîtes produites par le détecteur (bloc_detection/suggest.py).
-- Mono-classe (pas de label) : ce sont juste des propositions de découpe que
-- l'annotateur matérialise en vraies annotations puis reclasse.
CREATE TABLE IF NOT EXISTS suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id   INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    x0         INTEGER NOT NULL,
    y0         INTEGER NOT NULL,
    x1         INTEGER NOT NULL,
    y1         INTEGER NOT NULL,
    conf       REAL,
    model      TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sugg_image ON suggestions(image_id);

-- Propositions de corrections d'annotation (mode revision, cf.
-- bloc_detection/review_proposals.py). Chaque proposition dit quelles annotations
-- retirer (before) et lesquelles creer (after), au format JSON, pour appliquer
-- la convention d'annotation. L'utilisateur accepte / refuse / corrige.
CREATE TABLE IF NOT EXISTS proposals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id   INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    ptype      TEXT NOT NULL,          -- split_title / reclassify
    descr      TEXT,                   -- libelle lisible
    payload    TEXT NOT NULL,          -- JSON {before:[...], after:[...], region:[...], conf:..}
    status     TEXT DEFAULT 'pending', -- pending / accepted / rejected
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prop_image ON proposals(image_id);
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(reset_labels: bool = False):
    conn = get_conn()
    conn.executescript(SCHEMA)
    if reset_labels:
        # Reset labels (utile si on change le set des labels par defaut).
        # On supprime d'abord les annotations rattachees aux labels qu'on va
        # virer (cf. ON DELETE CASCADE manquante sur cette FK).
        conn.execute("DELETE FROM annotations")
        conn.execute("DELETE FROM labels")
    # Migrations de noms : preserver le label_id (et donc les annotations)
    # en renommant sur place. Ne s'applique que si la cible n'existe pas deja.
    existing = {r["name"] for r in conn.execute("SELECT name FROM labels")}
    for old, new in LABEL_RENAMES:
        if old in existing and new not in existing:
            conn.execute("UPDATE labels SET name = ? WHERE name = ?", (new, old))
            existing.discard(old)
            existing.add(new)
    # Seed labels
    for name, color, desc in DEFAULT_LABELS:
        conn.execute(
            "INSERT OR IGNORE INTO labels (name, color, description) VALUES (?, ?, ?)",
            (name, color, desc),
        )
    conn.commit()
    conn.close()


def list_images_paginated(limit: int = 20,
                            statuses: tuple[str, ...] = ("todo", "in_progress")) -> list[dict]:
    """Liste les N premieres unes dans les statuts donnes (FIFO par id)."""
    conn = get_conn()
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT * FROM images
            WHERE status IN ({placeholders})
            ORDER BY status DESC, id ASC
            LIMIT ?""",
        (*statuses, limit),
    ).fetchall()
    out = [dict(r) for r in rows]
    for img in out:
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM annotations WHERE image_id = ?",
            (img["id"],)
        ).fetchone()
        img["n_annotations"] = cnt["n"]
    conn.close()
    return out


def count_todo() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM images WHERE status='todo'").fetchone()["n"]
    conn.close()
    return n


def existing_slugs() -> set[str]:
    """Slugs deja en DB (pour eviter les doublons en telechargement)."""
    conn = get_conn()
    rows = conn.execute("SELECT slug FROM images").fetchall()
    conn.close()
    return {r["slug"] for r in rows}


def count_by_journal(status: str | None = None) -> dict[str, int]:
    """Nombre d'unes par journal (titre), filtré sur un statut si fourni.
    Sert à équilibrer le téléchargement vers les journaux sous-représentés."""
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT journal, COUNT(*) AS n FROM images WHERE status = ? GROUP BY journal",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT journal, COUNT(*) AS n FROM images GROUP BY journal"
        ).fetchall()
    conn.close()
    return {r["journal"]: r["n"] for r in rows}


def add_image(slug: str, journal: str, iso_date: str, catalog_ark: str,
               issue_ark: str | None, path: str, w: int, h: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO images
           (slug, journal, iso_date, catalog_ark, issue_ark, path, w, h, downloaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (slug, journal, iso_date, catalog_ark, issue_ark, path, w, h, int(time.time())),
    )
    image_id = cur.lastrowid
    if image_id == 0:
        # Existait deja
        row = conn.execute("SELECT id FROM images WHERE slug = ?", (slug,)).fetchone()
        image_id = row["id"]
    conn.commit()
    conn.close()
    return image_id


def list_images(status: str | None = None) -> list[dict]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM images WHERE status = ? ORDER BY journal, iso_date",
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM images ORDER BY status, journal, iso_date"
        ).fetchall()
    out = [dict(r) for r in rows]
    # Compter les annotations
    for img in out:
        cnt = conn.execute(
            "SELECT COUNT(*) AS n FROM annotations WHERE image_id = ?",
            (img["id"],)
        ).fetchone()
        img["n_annotations"] = cnt["n"]
    conn.close()
    return out


def get_image(image_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_image_status(image_id: int, status: str):
    conn = get_conn()
    conn.execute("UPDATE images SET status = ? WHERE id = ?", (status, image_id))
    conn.commit()
    conn.close()


def list_labels() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM labels ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_annotations(image_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT a.*, l.name AS label_name, l.color AS label_color
           FROM annotations a JOIN labels l ON a.label_id = l.id
           WHERE a.image_id = ?
           ORDER BY a.id""",
        (image_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_annotation(image_id: int, label_id: int,
                    x0: int, y0: int, x1: int, y1: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO annotations (image_id, label_id, x0, y0, x1, y1, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (image_id, label_id, x0, y0, x1, y1, int(time.time())),
    )
    anno_id = cur.lastrowid
    conn.commit()
    conn.close()
    return anno_id


def delete_annotation(anno_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM annotations WHERE id = ?", (anno_id,))
    conn.commit()
    conn.close()


def update_annotation(anno_id: int, x0: int, y0: int, x1: int, y1: int,
                       label_id: int | None = None):
    conn = get_conn()
    if label_id is not None:
        conn.execute(
            "UPDATE annotations SET x0=?, y0=?, x1=?, y1=?, label_id=? WHERE id=?",
            (x0, y0, x1, y1, label_id, anno_id),
        )
    else:
        conn.execute(
            "UPDATE annotations SET x0=?, y0=?, x1=?, y1=? WHERE id=?",
            (x0, y0, x1, y1, anno_id),
        )
    conn.commit()
    conn.close()


def replace_suggestions(image_id: int, boxes: list[tuple], model: str = "") -> int:
    """Remplace les suggestions d'une image. boxes = [(x0,y0,x1,y1,conf), ...]."""
    conn = get_conn()
    conn.execute("DELETE FROM suggestions WHERE image_id = ?", (image_id,))
    now = int(time.time())
    conn.executemany(
        """INSERT INTO suggestions (image_id, x0, y0, x1, y1, conf, model, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(image_id, int(x0), int(y0), int(x1), int(y1), float(conf), model, now)
         for (x0, y0, x1, y1, conf) in boxes],
    )
    conn.commit()
    conn.close()
    return len(boxes)


def list_suggestions(image_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM suggestions WHERE image_id = ? ORDER BY conf DESC",
        (image_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def apply_suggestions(image_id: int, label_id: int) -> list[int]:
    """Matérialise les suggestions en annotations (label donné) puis les efface.

    Renvoie la liste des ids d'annotations créées (utile pour l'undo côté UI).
    L'annotateur reclasse/ajuste ensuite avec les outils habituels.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT x0, y0, x1, y1 FROM suggestions WHERE image_id = ?",
        (image_id,)
    ).fetchall()
    now = int(time.time())
    ids = []
    for r in rows:
        cur = conn.execute(
            """INSERT INTO annotations (image_id, label_id, x0, y0, x1, y1, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (image_id, label_id, r["x0"], r["y0"], r["x1"], r["y1"], now),
        )
        ids.append(cur.lastrowid)
    conn.execute("DELETE FROM suggestions WHERE image_id = ?", (image_id,))
    conn.commit()
    conn.close()
    return ids


# ── Propositions de correction (mode révision) ──────────────────────────────

def replace_proposals(image_id: int, proposals: list[dict]) -> int:
    """Remplace les propositions 'pending' d'une image. Chaque proposition :
    {ptype, descr, payload(dict)}. Les propositions déjà traitées (accepted/
    rejected) sont conservées."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM proposals WHERE image_id = ? AND status = 'pending'", (image_id,)
    )
    now = int(time.time())
    conn.executemany(
        """INSERT INTO proposals (image_id, ptype, descr, payload, status, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?)""",
        [(image_id, p["ptype"], p.get("descr", ""), json.dumps(p["payload"]), now)
         for p in proposals],
    )
    conn.commit()
    conn.close()
    return len(proposals)


def list_proposals(image_id: int, status: str = "pending") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM proposals WHERE image_id = ? AND status = ? ORDER BY id",
        (image_id, status),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def list_all_proposals(status: str = "pending") -> list[dict]:
    """Toutes les propositions d'un statut, avec les infos de leur une (slug,
    journal, date) — pour l'onglet Corrections global."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*, i.slug, i.journal, i.iso_date
           FROM proposals p JOIN images i ON i.id = p.image_id
           WHERE p.status = ?
           ORDER BY p.image_id, p.id""",
        (status,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def get_proposal(pid: int) -> dict | None:
    conn = get_conn()
    r = conn.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


def set_proposal_status(pid: int, status: str):
    conn = get_conn()
    conn.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, pid))
    conn.commit()
    conn.close()


def count_proposals(image_id: int, status: str = "pending") -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM proposals WHERE image_id = ? AND status = ?",
        (image_id, status),
    ).fetchone()["n"]
    conn.close()
    return n


def stats() -> dict:
    conn = get_conn()
    tot   = conn.execute("SELECT COUNT(*) AS n FROM images").fetchone()["n"]
    done  = conn.execute("SELECT COUNT(*) AS n FROM images WHERE status='done'").fetchone()["n"]
    in_p  = conn.execute("SELECT COUNT(*) AS n FROM images WHERE status='in_progress'").fetchone()["n"]
    todo  = conn.execute("SELECT COUNT(*) AS n FROM images WHERE status='todo'").fetchone()["n"]
    annos = conn.execute("SELECT COUNT(*) AS n FROM annotations").fetchone()["n"]
    by_label = conn.execute(
        """SELECT l.name, COUNT(a.id) AS n
           FROM labels l LEFT JOIN annotations a ON a.label_id = l.id
           GROUP BY l.id ORDER BY n DESC"""
    ).fetchall()
    conn.close()
    return {
        "total": tot, "done": done, "in_progress": in_p, "todo": todo,
        "annotations": annos,
        "by_label": {r["name"]: r["n"] for r in by_label},
    }


if __name__ == "__main__":
    init_db()
    print("DB initialisee :", DB_PATH)
    print("Labels :", [l["name"] for l in list_labels()])
