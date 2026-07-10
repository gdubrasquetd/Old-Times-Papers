"""Étape 4 : regroupe les blocs d'une une en ARTICLES (titre + son corps).

L'ordre de lecture produit par detect.py est un simple tri (y, x) : sur une page à
6 colonnes il saute d'une colonne à l'autre. On reconstruit donc l'ordre à partir de
la géométrie : détection des colonnes, fusion des titres multi-lignes, puis parcours
colonne par colonne où un titre « ouvre » un article pour les colonnes qu'il couvre.

Écrit aussi un PNG de contrôle (une couleur par article) pour valider visuellement.

    python articles.py <blocks.json> [articles.png]
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

from blocks_util import dedup_blocks, paper_name, paper_date

TEXT_CLASSES = {"bloc de texte", "titre", "texte isolé"}
BODY_CLASSES = {"bloc de texte", "texte isolé"}

# Bruit de manchette : reconnaissable partout (mentions légales, tarifs)
STRONG_NOISE = [
    r"ne repond pas des manuscrits", r"manuscrits ne sont pas rendus",
    r"decline toute responsabilite", r"prix de l'? ?abonnement",
    r"adresse telegraphique", r"cheque postal", r"agence havas",
]
# Reconnaissable seulement dans la bande haute (ces mots existent aussi en article).
# `etranger` en est volontairement absent : « Nouvelles de l'Étranger » est une vraie rubrique.
TOPBAND_NOISE = [
    r"\babonnements?\b", r"\bannonces\b", r"\breclames?\b", r"\bpublicite\b",
    r"\bredaction\b", r"\badministration\b", r"\btelephone\b", r"\bfondateur\b",
    r"\bsecretariat\b", r"anciens directeurs", r"\bdirecteurs?\b",
    r"bureaux du", r"\brue des\b", r"\bsix mois\b", r"\btrois mois\b", r"\bun an\b",
]
STRONG_RE = re.compile("|".join(STRONG_NOISE))
TOPBAND_RE = re.compile("|".join(TOPBAND_NOISE))

# Les encarts d'abonnement/annonces descendent jusqu'à ~0,135 H ; le premier vrai
# contenu (« SOMMAIRE », un titre) commence vers 0,142 H. D'où ces deux bandes.
PATTERN_BAND_FRAC = 0.14  # les motifs TOPBAND ne s'appliquent que là
SHORT_BAND_FRAC = 0.10    # plus haut encore : date, numéro d'édition, noms des directeurs
SHORT_TOP_CHARS = 60

PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#008080",
           "#9a6324", "#800000", "#808000", "#000075", "#f032e6", "#46f0f0"]


# ───────────────────────────── helpers géométrie ────────────────────────

def _fold(s):
    """minuscules sans accents, pour comparer du texte OCR d'époque."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _yc(b):
    return (b["box"][1] + b["box"][3]) / 2


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


# ───────────────────────────── filtrage du bruit ────────────────────────

def is_masthead_noise(block, img_w, img_h):
    """True si le bloc est de la manchette / mention légale / sans texte utile."""
    cls = block.get("class")
    if cls in ("header", "illustration"):
        return True
    txt = (block.get("text") or "").strip()
    if not txt:
        return True
    folded = _fold(txt)
    if STRONG_RE.search(folded):
        return True
    yf = _yc(block) / img_h if img_h else 1.0
    if yf < PATTERN_BAND_FRAC and TOPBAND_RE.search(folded):
        return True
    if yf < SHORT_BAND_FRAC and len(txt) < SHORT_TOP_CHARS:
        return True
    return False


# ───────────────────────────── colonnes ─────────────────────────────────

def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2]


def detect_columns(blocks, img_w, *, tol_frac=0.4, span_factor=1.5):
    """Infère les colonnes à partir des BORDS GAUCHES des blocs `bloc de texte`.

    Une carte de couverture ne marche pas : les boîtes détectées débordent de quelques
    pixels dans la gouttière (mesurée à 3-18 px), si bien que les colonnes se touchent.
    En revanche les x0 se groupent nettement par colonne (ex. 121,147 | 921,928,932 | …)
    et la largeur de colonne est très régulière.

    On écarte les blocs manifestement à cheval (largeur > span_factor × médiane), on
    groupe les x0 restants par chaînage (écart <= tol_frac × largeur médiane), puis
    chaque groupe donne une colonne (médiane des x0, + largeur médiane).
    Renvoie [(xmin, xmax), ...] ordonné de gauche à droite.
    """
    if img_w <= 0:
        return []
    body = [b for b in blocks if b.get("class") == "bloc de texte"]
    if not body:
        body = [b for b in blocks if b.get("class") in BODY_CLASSES]
    if not body:
        return []

    wmed = _median([b["box"][2] - b["box"][0] for b in body])
    if wmed <= 0:
        return []
    single = [b for b in body if (b["box"][2] - b["box"][0]) <= span_factor * wmed] or body

    xs = sorted(b["box"][0] for b in single)
    tol = tol_frac * wmed
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    return [(_median(c), min(img_w, _median(c) + wmed)) for c in clusters]


def assign_columns(box, columns, *, cover_frac=0.5):
    """(première, dernière) colonne couverte. Un titre chapeau renvoie (a, b) avec b > a.
    Un bloc étroit retombe sur la colonne contenant son centre."""
    if not columns:
        return (0, 0)
    x0, x1 = box[0], box[2]
    covered = []
    for i, (c0, c1) in enumerate(columns):
        ov = max(0.0, min(x1, c1) - max(x0, c0))
        cw = c1 - c0
        if cw > 0 and ov >= cover_frac * cw:
            covered.append(i)
    if covered:
        return (covered[0], covered[-1])
    xc = (x0 + x1) / 2
    best, bdist = 0, float("inf")
    for i, (c0, c1) in enumerate(columns):
        if c0 <= xc <= c1:
            return (i, i)
        d = min(abs(xc - c0), abs(xc - c1))
        if d < bdist:
            bdist, best = d, i
    return (best, best)


# ───────────────────────────── titres ───────────────────────────────────

def merge_headline_fragments(titres, columns, img_h, *, vgap_frac=0.02, hov_frac=0.3):
    """Fusionne les blocs `titre` empilés d'un même gros titre (même emprise de
    colonnes, recouvrement horizontal, faible écart vertical)."""
    vmax = vgap_frac * img_h
    groups = []
    for t in sorted(titres, key=lambda b: (b["box"][1], b["box"][0])):
        span = assign_columns(t["box"], columns)
        placed = False
        for g in groups:
            if g["columns"] != span:
                continue
            if t["box"][1] - g["box"][3] > vmax:
                continue
            ov = max(0.0, min(t["box"][2], g["box"][2]) - max(t["box"][0], g["box"][0]))
            minw = min(t["box"][2] - t["box"][0], g["box"][2] - g["box"][0])
            if minw > 0 and ov >= hov_frac * minw:
                g["blocks"].append(t)
                g["box"] = _union([g["box"], t["box"]])
                placed = True
                break
        if not placed:
            groups.append({"columns": span, "box": list(t["box"]), "blocks": [t]})
    for g in groups:
        g["text"] = "\n".join(_dedup_lines(
            (b.get("text") or "").strip() for b in g["blocks"]))
    return groups


def _dedup_lines(lines):
    """Un même gros titre est parfois détecté deux fois, l'une des lectures étant
    tronquée ('Mesures de précaution' + 'Mesures de précaution contre le bombardement').
    On retire les lignes vides, les doublons exacts et celles contenues dans une autre."""
    lines = [l for l in lines if l.strip()]
    folded = [_fold(l) for l in lines]
    out, seen = [], set()
    for i, l in enumerate(lines):
        f = folded[i]
        if f in seen:
            continue
        if any(j != i and f != folded[j] and f in folded[j] for j in range(len(lines))):
            continue
        seen.add(f)
        out.append(l)
    return out


# ───────────────────────────── regroupement ─────────────────────────────

def group_articles(blocks, img_w, img_h):
    """blocks.json -> {"articles": [...], "columns": [...], "dropped": [ids]}"""
    blocks, _ = dedup_blocks(blocks)
    kept, dropped = [], []
    for b in blocks:
        (dropped if is_masthead_noise(b, img_w, img_h) else kept).append(b)
    kept = [b for b in kept if b.get("class") in TEXT_CLASSES]

    columns = detect_columns(kept, img_w)
    body = [b for b in kept if b["class"] in BODY_CLASSES]
    titres = [b for b in kept if b["class"] == "titre"]
    headlines = merge_headline_fragments(titres, columns, img_h)

    ncols = max(1, len(columns))
    # titres visibles depuis chaque colonne, de haut en bas
    head_by_col = {k: [] for k in range(ncols)}
    for hi, h in enumerate(headlines):
        a, b = h["columns"]
        for k in range(a, b + 1):
            if k in head_by_col:
                head_by_col[k].append(hi)
    for k in head_by_col:
        head_by_col[k].sort(key=lambda hi: headlines[hi]["box"][1])

    members = {hi: [] for hi in range(len(headlines))}   # article -> blocs de corps
    orphans = {k: [] for k in range(ncols)}

    for k in range(ncols):
        col_body = [b for b in body if assign_columns(b["box"], columns)[0] == k]
        col_body.sort(key=lambda b: b["box"][1])
        for b in col_body:
            cur = None
            for hi in head_by_col[k]:
                if headlines[hi]["box"][1] <= b["box"][1]:
                    cur = hi
                else:
                    break
            if cur is None:
                orphans[k].append(b)
            else:
                members[cur].append(b)

    articles = []
    for hi, h in enumerate(headlines):
        blks = sorted(members[hi], key=lambda b: (assign_columns(b["box"], columns)[0], b["box"][1]))
        boxes = [h["box"]] + [b["box"] for b in blks]
        articles.append({"headline": h["text"], "headline_box": h["box"],
                         "columns": list(h["columns"]), "blocks": blks,
                         "box": _union(boxes)})
    for k in range(ncols):
        if orphans[k]:
            blks = sorted(orphans[k], key=lambda b: b["box"][1])
            articles.append({"headline": "", "headline_box": None,
                             "columns": [k, k], "blocks": blks,
                             "box": _union([b["box"] for b in blks])})

    # ordre de lecture des articles : haut -> bas, puis gauche -> droite
    articles.sort(key=lambda a: (a["box"][1], a["columns"][0]))
    for i, a in enumerate(articles):
        a["id"] = i
        a["block_ids"] = [b["id"] for b in a["blocks"]]

    return {"articles": articles, "columns": columns,
            "dropped": [b["id"] for b in dropped]}


def article_text(article):
    """Corps de l'article dans l'ordre de lecture reconstruit."""
    return "\n".join((b.get("text") or "").strip() for b in article["blocks"]
                     if (b.get("text") or "").strip())


# ───────────────────────────── rendu de contrôle ────────────────────────

def resolve_image(data, blocks_path=None):
    """Le champ `image` de blocks.json peut être périmé (chemins d'avant la réorg).
    On retombe sur block-detection/annotation/data/images/<slug>.jpg."""
    p = Path(data.get("image", ""))
    if p.exists():
        return p
    root = Path(__file__).resolve().parent.parent
    alt = root / "block-detection" / "annotation" / "data" / "images" / f"{data['slug']}.jpg"
    return alt if alt.exists() else None


def render_articles_png(data, grouped, out_path, *, target_h=1400):
    """Une couleur par article ; titre encadré plus épais + étiquette A0, A1…
    Blocs écartés en gris barré. Colonnes en pointillés."""
    from PIL import Image, ImageDraw, ImageFont     # lazy : pas de PIL dans l'env LLM

    src = resolve_image(data)
    if src is None:
        raise FileNotFoundError(f"image introuvable pour {data['slug']}")
    im = Image.open(src).convert("RGB")
    W, H = data["img_w"], data["img_h"]
    scale = target_h / H
    im = im.resize((round(W * scale), target_h), Image.LANCZOS)
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    def S(box):
        return [box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale]

    def rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    for (c0, c1) in grouped["columns"]:            # gouttières
        for x in (c0 * scale, c1 * scale):
            for y in range(0, target_h, 14):
                d.line([(x, y), (x, y + 7)], fill=(0, 0, 0, 90), width=1)

    dropped = set(grouped["dropped"])
    for b in data["blocks"]:
        if b["id"] in dropped:
            x0, y0, x1, y1 = S(b["box"])
            d.rectangle([x0, y0, x1, y1], fill=(130, 130, 130, 60), outline=(90, 90, 90, 200))
            d.line([x0, y0, x1, y1], fill=(90, 90, 90, 160), width=1)

    try:
        font = ImageFont.truetype("arialbd.ttf", 17)
    except OSError:
        font = ImageFont.load_default()

    for a in grouped["articles"]:
        col = rgb(PALETTE[a["id"] % len(PALETTE)])
        for b in a["blocks"]:
            d.rectangle(S(b["box"]), fill=col + (55,), outline=col + (255,), width=2)
        if a["headline_box"]:
            d.rectangle(S(a["headline_box"]), fill=col + (95,), outline=col + (255,), width=4)
        lx, ly = S(a["box"])[0], S(a["box"])[1]
        label = f"A{a['id']}"
        d.rectangle([lx, ly, lx + 34, ly + 21], fill=col + (235,))
        d.text((lx + 4, ly + 2), label, fill=(255, 255, 255, 255), font=font)

    out = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    out.save(out_path)
    return out_path


# ───────────────────────────── CLI ──────────────────────────────────────

def main():
    blocks_path = Path(sys.argv[1])
    data = json.loads(blocks_path.read_text(encoding="utf-8"))
    grouped = group_articles(data["blocks"], data["img_w"], data["img_h"])

    n_body = sum(len(a["blocks"]) for a in grouped["articles"])
    n_breves = sum(1 for a in grouped["articles"] if not a["headline"])
    print(f"{paper_name(data['slug'])} {paper_date(data['slug']) or ''} — "
          f"{len(grouped['columns'])} colonnes, {len(grouped['articles'])} articles "
          f"({n_breves} brèves), {n_body} blocs de corps, "
          f"{len(grouped['dropped'])} blocs écartés", flush=True)
    for a in grouped["articles"][:12]:
        head = (a["headline"] or "(brève)").replace("\n", " ")[:60]
        print(f"  A{a['id']:<2} col{a['columns']} {len(a['blocks']):>2} blocs  {head}", flush=True)

    if len(sys.argv) > 2:
        out = render_articles_png(data, grouped, Path(sys.argv[2]))
        print(f"  -> {out}", flush=True)


if __name__ == "__main__":
    main()
