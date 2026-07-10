"""Fonctions partagées autour de blocks.json — stdlib uniquement.

Extrait de build.py pour que les étapes qui n'ont pas besoin de PIL (articles.py,
summarize.py, qui tournent dans d'autres envs) puissent réutiliser ces fonctions
sans tirer Pillow. build.py les ré-exporte : les imports existants restent valides.
"""
import re

PAPERS = {
    "le_figaro": "Le Figaro", "le_temps": "Le Temps", "le_matin": "Le Matin",
    "le_journal": "Le Journal", "le_gaulois": "Le Gaulois", "petit_journal": "Le Petit Journal",
    "petit_parisien": "Le Petit Parisien", "intransigeant": "L'Intransigeant",
    "humanite": "L'Humanité", "action_francaise": "L'Action Française",
    "journal_des_debats": "Journal des Débats", "echo_de_paris": "L'Écho de Paris",
    "excelsior": "Excelsior", "la_croix": "La Croix", "oeuvre": "L'Œuvre",
    "populaire": "Le Populaire",
}
COLORS = {"header": "#8b2c2c", "titre": "#c0392b", "bloc de texte": "#1a4a7a",
          "texte isolé": "#7d3c98", "illustration": "#5a6b3a", "autres": "#777"}

DATE_SUFFIX_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})$")


def paper_name(slug):
    key = DATE_SUFFIX_RE.sub("", slug)
    return PAPERS.get(key, key.replace("_", " ").title())


def paper_date(slug):
    """Extrait la date ISO du slug ('le_temps_1936-08-08' -> '1936-08-08'), sinon None."""
    m = DATE_SUFFIX_RE.search(slug or "")
    return m.group(1) if m else None


def _iou(a, b):
    ax0, ay0, ax1, ay1 = a["box"]; bx0, by0, bx1, by1 = b["box"]
    inter = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    if not inter:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua else 0.0


def dedup_blocks(blocks, iou_thresh=0.6):
    """Retire les blocs quasi superposés (IoU > seuil) portant le MÊME texte ;
    garde celui de meilleure confiance. Renvoie (blocs_gardés, nb_retirés)."""
    def _nt(s):
        return re.sub(r"\s+", "", (s or "").lower())
    drop = set()
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            if i in drop or j in drop:
                continue
            ti, tj = _nt(blocks[i].get("text")), _nt(blocks[j].get("text"))
            if ti and ti == tj and _iou(blocks[i], blocks[j]) > iou_thresh:
                drop.add(j if blocks[i].get("conf", 0) >= blocks[j].get("conf", 0) else i)
    return [b for k, b in enumerate(blocks) if k not in drop], len(drop)
