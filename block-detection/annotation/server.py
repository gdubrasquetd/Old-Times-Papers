"""
Serveur d'annotation. UI web canvas pour dessiner des bbox sur les unes.
Run : python server.py  -> http://localhost:5050
"""
from __future__ import annotations
import sys, os, pathlib, io, json, threading, time, logging, subprocess
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Console Windows = cp1252 : un print accentué (logs replenish, [client] verbeux)
# lèverait UnicodeEncodeError et tuerait le serveur. On force l'utf-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flask import Flask, render_template, request, jsonify, send_file, abort, redirect, url_for
from PIL import Image

import db
import downloader

ROOT      = pathlib.Path(__file__).parent
IMG_DIR   = ROOT / "data" / "images"
THUMB_DIR = ROOT / "data" / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)

# Détection à la demande (bouton dans l'UI) : le serveur tourne dans l'env
# `oldspapers` (sans torch), il shelle donc vers le python de `bloc_detection`
# qui exécute suggest.py sur l'image courante. Surchageable par variables d'env.
BLOC_DIR    = ROOT.parent / "training"
BLOC_PYTHON = os.environ.get(
    "BLOC_DETECTION_PYTHON",
    str(pathlib.Path.home() / ".conda" / "envs" / "bloc_detection" / "python.exe"),
)
DETECT_CONF = os.environ.get("BLOC_DETECTION_CONF", "0.4")
# Recouvrement (intersection/aire de la plus petite) au-delà duquel une boîte
# proposée est supprimée comme doublon. 0 = désactivé. Défaut = celui de suggest.py.
DETECT_DEDUP = os.environ.get("BLOC_DETECTION_DEDUP", "0.85")

# Pagination
PAGE_SIZE       = 10    # nb d'unes affichees sur la page de travail
TARGET_POOL     = 20    # taille cible du pool todo (cache d'avance)
LOW_WATERMARK   = 8     # si moins de N todo, on declenche un replenish

# Thumbnails (grille). Une une 5000x7000 = ~140 MB une fois decodee par le
# navigateur ; reduire a 400 px de large fait tomber la grille de ~1.4 GB
# de bitmap a ~10-20 MB.
THUMB_WIDTH = 400

app = Flask(__name__, template_folder=str(ROOT / "templates"),
             static_folder=str(ROOT / "static"))
# Recharge les templates à chaud (pas de cache Jinja) : modif HTML sans redémarrage.
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Etat replenisseur (un seul tache en cours a la fois)
_replenish_lock = threading.Lock()
_replenishing = False

def _replenish_in_background():
    """Telecharge jusqu'a atteindre TARGET_POOL unes en statut todo."""
    global _replenishing
    with _replenish_lock:
        if _replenishing:
            return
        _replenishing = True
    try:
        needed = max(0, TARGET_POOL - db.count_todo())
        if needed > 0:
            n = downloader.download_batch(n=needed,
                                           year_range=(1900, 1939),
                                           delay=1.5, shuffle=True,
                                           log=lambda m: print(m, flush=True))
            print(f"[replenish] {n}/{needed} nouvelles unes ajoutees", flush=True)
    except Exception as e:
        print(f"[replenish] ERR : {e}", flush=True)
    finally:
        with _replenish_lock:
            _replenishing = False


def _maybe_trigger_replenish():
    """Lance un replenish en arriere-plan si pool todo trop bas."""
    if db.count_todo() < LOW_WATERMARK:
        # Lancer un thread (non bloquant pour la requete HTTP)
        threading.Thread(target=_replenish_in_background, daemon=True).start()


# Etat calcul des propositions de correction (batch sur toutes les unes)
_review_lock = threading.Lock()
_reviewing = False
_review_msg = ""

def _review_all_in_background():
    """Calcule les propositions de correction sur toutes les unes 'done'
    (modele charge une seule fois cote bloc_detection)."""
    global _reviewing, _review_msg
    with _review_lock:
        if _reviewing:
            return
        _reviewing = True
        _review_msg = "en cours…"
    try:
        cmd = [BLOC_PYTHON, str(BLOC_DIR / "review_proposals.py"), "--all", "--status", "done"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1200)
        if proc.returncode != 0:
            _review_msg = "erreur : " + (proc.stderr or proc.stdout)[-200:]
        else:
            _review_msg = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else "terminé"
        print(f"[review] {_review_msg}", flush=True)
    except Exception as e:
        _review_msg = f"erreur : {e}"
        print(f"[review] ERR : {e}", flush=True)
    finally:
        with _review_lock:
            _reviewing = False


# ─── Pages ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    # On affiche en priorite todo + in_progress (page de travail).
    # Filtre journal optionnel.
    journal = request.args.get("journal")
    view    = request.args.get("view", "work")  # 'work' | 'in_progress' | 'done' | 'all'

    if view == "done":
        all_imgs = db.list_images(status="done")
    elif view == "in_progress":
        all_imgs = db.list_images(status="in_progress")
    elif view == "all":
        all_imgs = db.list_images()
    else:
        all_imgs = db.list_images_paginated(limit=PAGE_SIZE,
                                              statuses=("in_progress", "todo"))

    if journal:
        all_imgs = [i for i in all_imgs if i["journal"] == journal]

    images = all_imgs[:PAGE_SIZE] if view == "work" else all_imgs

    stats = db.stats()
    journals = sorted(set(i["journal"] for i in db.list_images()))
    # Declenche un replenish en arriere-plan si necessaire
    _maybe_trigger_replenish()
    return render_template("index.html",
                            images=images, stats=stats, view=view,
                            journal_filter=journal, journals=journals,
                            page_size=PAGE_SIZE,
                            replenishing=_replenishing)


@app.route("/annotate/<int:image_id>")
def annotate(image_id):
    img = db.get_image(image_id)
    if not img:
        abort(404)
    labels = db.list_labels()
    if img["status"] == "todo":
        db.set_image_status(image_id, "in_progress")
    return render_template("annotate.html", image=img, labels=labels)


@app.route("/corrections")
def corrections():
    """Onglet global de révision : toutes les propositions de correction."""
    return render_template("corrections.html", labels=db.list_labels())


# ─── API ────────────────────────────────────────────────────────────────
@app.route("/api/image/<int:image_id>/file")
def serve_image(image_id):
    img = db.get_image(image_id)
    if not img:
        abort(404)
    path = pathlib.Path(img["path"])
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/jpeg")


@app.route("/api/image/<int:image_id>/crop")
def serve_crop(image_id):
    """Renvoie un crop JPEG d'une région (box=x0,y0,x1,y1) redimensionné à w px
    de large. Sert les vignettes avant/après de l'onglet Corrections sans charger
    l'image entière (68 Mpx) côté navigateur."""
    img = db.get_image(image_id)
    if not img:
        abort(404)
    path = pathlib.Path(img["path"])
    if not path.exists():
        abort(404)
    try:
        x0, y0, x1, y1 = (int(float(v)) for v in request.args.get("box", "").split(","))
    except Exception:
        abort(400)
    maxw = int(request.args.get("w", 300))
    maxh = int(request.args.get("h", 340))
    im = Image.open(path)
    x0 = max(0, x0); y0 = max(0, y0); x1 = min(im.width, x1); y1 = min(im.height, y1)
    if x1 <= x0 or y1 <= y0:
        abort(400)
    crop = im.crop((x0, y0, x1, y1))
    scale = min(maxw / crop.width, maxh / crop.height)
    if scale < 1:
        crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, "JPEG", quality=82)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg")


@app.route("/api/image/<int:image_id>/thumb")
def serve_thumb(image_id):
    """Sert une vignette ~400 px de large (genere puis cache sur disque).

    Utilise par la grille de la page d'accueil pour eviter de pousser des
    JPEG full-res au navigateur (decompresses, une une = ~140 MB en RAM).
    """
    img = db.get_image(image_id)
    if not img:
        abort(404)
    src = pathlib.Path(img["path"])
    if not src.exists():
        abort(404)
    thumb = THUMB_DIR / f"{img['slug']}.jpg"
    if not thumb.exists():
        # Genere atomiquement : on ecrit dans un .tmp puis on renomme,
        # pour qu'une requete concurrente ne lise pas un fichier partiel.
        tmp = thumb.with_suffix(".jpg.tmp")
        with Image.open(src) as im:
            im.thumbnail((THUMB_WIDTH, 10_000), Image.LANCZOS)
            im.convert("RGB").save(tmp, "JPEG", quality=85, optimize=True)
        os.replace(tmp, thumb)
    # Cache-control long : le contenu d'une thumb par slug ne change jamais.
    resp = send_file(str(thumb), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


PERF_LOG = ROOT / "data" / "perf.log"


@app.route("/api/health")
def health():
    """Check de santé léger (sans accès lourd) pour le watchdog."""
    return jsonify({"ok": True, "ts": int(time.time())})


@app.route("/api/clientlog", methods=["POST"])
def client_log():
    """Reçoit les logs perf du frontend (lags) et les imprime + persiste avec un
    horodatage, pour pouvoir détecter les à-coups après coup."""
    msg = request.get_data(as_text=True)[:500]
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(f"[client] {msg}", flush=True)
    try:
        with open(PERF_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    return ("", 204)


@app.route("/api/image/<int:image_id>/annotations")
def get_annotations(image_id):
    return jsonify(db.list_annotations(image_id))


@app.route("/api/annotations", methods=["POST"])
def post_annotation():
    data = request.get_json()
    anno_id = db.add_annotation(
        image_id=int(data["image_id"]),
        label_id=int(data["label_id"]),
        x0=int(data["x0"]), y0=int(data["y0"]),
        x1=int(data["x1"]), y1=int(data["y1"]),
    )
    return jsonify({"id": anno_id})


@app.route("/api/annotations/<int:anno_id>", methods=["PUT"])
def put_annotation(anno_id):
    data = request.get_json()
    db.update_annotation(
        anno_id=anno_id,
        x0=int(data["x0"]), y0=int(data["y0"]),
        x1=int(data["x1"]), y1=int(data["y1"]),
        label_id=int(data["label_id"]) if "label_id" in data else None,
    )
    return jsonify({"ok": True})


@app.route("/api/annotations/<int:anno_id>", methods=["DELETE"])
def del_annotation(anno_id):
    db.delete_annotation(anno_id)
    return jsonify({"ok": True})


@app.route("/api/image/<int:image_id>/suggestions")
def get_suggestions(image_id):
    """Boîtes proposées par le détecteur (cf. bloc_detection/suggest.py)."""
    return jsonify(db.list_suggestions(image_id))


@app.route("/api/image/<int:image_id>/detect", methods=["POST"])
def detect_blocks(image_id):
    """Lance le détecteur de blocs sur l'image courante (sous-processus env
    bloc_detection) et écrit les boîtes dans la table suggestions."""
    img = db.get_image(image_id)
    if not img:
        abort(404)
    if not pathlib.Path(BLOC_PYTHON).exists():
        return jsonify({"error": f"python bloc_detection introuvable : {BLOC_PYTHON}. "
                                 "Définis BLOC_DETECTION_PYTHON."}), 500
    data = request.get_json(silent=True) or {}
    conf = str(data.get("conf", DETECT_CONF))
    dedup = str(data.get("dedup", DETECT_DEDUP))
    cmd = [BLOC_PYTHON, str(BLOC_DIR / "suggest.py"),
           "--image-id", str(image_id), "--conf", conf, "--dedup", dedup]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "détection trop longue (timeout 180s)"}), 504
    if proc.returncode != 0:
        return jsonify({"error": "échec détection",
                        "detail": (proc.stderr or proc.stdout)[-500:]}), 500
    return jsonify({"count": len(db.list_suggestions(image_id))})


@app.route("/api/image/<int:image_id>/apply-suggestions", methods=["POST"])
def apply_suggestions(image_id):
    """Matérialise les suggestions en annotations avec le label fourni."""
    data = request.get_json() or {}
    label_id = int(data["label_id"])
    ids = db.apply_suggestions(image_id, label_id)
    return jsonify({"created": len(ids), "ids": ids})


# ── Mode révision : propositions de correction selon la convention ──────────

@app.route("/api/image/<int:image_id>/review/compute", methods=["POST"])
def review_compute(image_id):
    """Lance review_proposals.py (env bloc_detection) pour (re)calculer les
    propositions de correction d'annotation de cette une."""
    img = db.get_image(image_id)
    if not img:
        abort(404)
    if not pathlib.Path(BLOC_PYTHON).exists():
        return jsonify({"error": f"python bloc_detection introuvable : {BLOC_PYTHON}."}), 500
    cmd = [BLOC_PYTHON, str(BLOC_DIR / "review_proposals.py"), "--image-id", str(image_id)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "révision trop longue (timeout 180s)"}), 504
    if proc.returncode != 0:
        return jsonify({"error": "échec révision",
                        "detail": (proc.stderr or proc.stdout)[-500:]}), 500
    return jsonify({"count": db.count_proposals(image_id)})


@app.route("/api/image/<int:image_id>/proposals")
def get_proposals(image_id):
    """Propositions de correction en attente pour cette une."""
    return jsonify(db.list_proposals(image_id, status="pending"))


@app.route("/api/proposals/<int:pid>/apply", methods=["POST"])
def apply_proposal(pid):
    """Applique une proposition : retire les annotations `before`, crée les
    `after`. Renvoie de quoi annuler côté client (créées + supprimées)."""
    p = db.get_proposal(pid)
    if not p:
        abort(404)
    if p["status"] != "pending":
        return jsonify({"error": "proposition déjà traitée"}), 409
    image_id = p["image_id"]
    deleted = []
    for b in p["payload"]["before"]:
        deleted.append(b)                       # contient déjà label_id + coords
        db.delete_annotation(b["id"])
    created = []
    for a in p["payload"]["after"]:
        aid = db.add_annotation(image_id, a["label_id"], a["x0"], a["y0"], a["x1"], a["y1"])
        created.append({"id": aid, "label_id": a["label_id"], "label_name": a.get("label_name"),
                        "x0": a["x0"], "y0": a["y0"], "x1": a["x1"], "y1": a["y1"]})
    db.set_proposal_status(pid, "accepted")
    return jsonify({"created": created, "deleted": deleted})


@app.route("/api/proposals/<int:pid>/reject", methods=["POST"])
def reject_proposal(pid):
    p = db.get_proposal(pid)
    if not p:
        abort(404)
    db.set_proposal_status(pid, "rejected")
    return jsonify({"ok": True})


# ── Onglet Corrections : calcul batch + liste globale ──

@app.route("/api/review/compute-all", methods=["POST"])
def review_compute_all():
    """Calcule (en arrière-plan) les propositions sur toutes les unes 'done'."""
    if not pathlib.Path(BLOC_PYTHON).exists():
        return jsonify({"error": f"python bloc_detection introuvable : {BLOC_PYTHON}."}), 500
    threading.Thread(target=_review_all_in_background, daemon=True).start()
    return jsonify({"ok": True, "reviewing": True})


@app.route("/api/review/status")
def review_status():
    return jsonify({"reviewing": _reviewing, "message": _review_msg,
                    "pending": len(db.list_all_proposals("pending"))})


@app.route("/api/proposals/all")
def all_proposals():
    return jsonify(db.list_all_proposals("pending"))


@app.route("/api/image/<int:image_id>/status", methods=["POST"])
def update_status(image_id):
    data = request.get_json()
    db.set_image_status(image_id, data["status"])
    return jsonify({"ok": True})


@app.route("/api/labels")
def get_labels():
    return jsonify(db.list_labels())


@app.route("/api/stats")
def get_stats():
    return jsonify(db.stats())


@app.route("/api/export")
def export_json():
    """Export toutes les annotations en JSON COCO-like."""
    images = db.list_images()
    labels = db.list_labels()
    out = {
        "info": {"description": "Oldspapers annotation dataset"},
        "labels": {l["name"]: {"id": l["id"], "color": l["color"]} for l in labels},
        "images": [],
    }
    for img in images:
        annos = db.list_annotations(img["id"])
        if not annos:
            continue
        out["images"].append({
            "id":          img["id"],
            "slug":        img["slug"],
            "journal":     img["journal"],
            "date":        img["iso_date"],
            "w":           img["w"],
            "h":           img["h"],
            "path":        img["path"],
            "status":      img["status"],
            "annotations": [{
                "id":    a["id"],
                "label": a["label_name"],
                "bbox":  [a["x0"], a["y0"], a["x1"], a["y1"]],
            } for a in annos],
        })
    return jsonify(out)


@app.route("/api/replenish", methods=["POST"])
def trigger_replenish():
    """Force un replenish (utile depuis l'UI)."""
    threading.Thread(target=_replenish_in_background, daemon=True).start()
    return jsonify({"ok": True, "replenishing": True})


@app.route("/api/replenish/status")
def replenish_status():
    return jsonify({
        "replenishing": _replenishing,
        "todo":         db.count_todo(),
        "low_watermark": LOW_WATERMARK,
    })


if __name__ == "__main__":
    db.init_db()
    # Calmer les logs flask
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"Serveur d'annotation : http://localhost:5050  (page size = {PAGE_SIZE})")
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
