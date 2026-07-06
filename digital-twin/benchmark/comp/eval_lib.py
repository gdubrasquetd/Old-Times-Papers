"""Bibliothèque partagée du grand comparatif : GT, blocs, métriques, post-traitements."""
import json, re, unicodedata
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
T_TEST = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")
TEXT = {"bloc de texte", "titre", "texte isolé"}


def blocklist():
    """[(file, crop_path, class, gt)] pour tous les blocs de texte dev+test."""
    items = []
    gd = json.loads((BENCH / "gt.json").read_text(encoding="utf-8"))
    for m in json.loads((BENCH / "manifest.json").read_text(encoding="utf-8")):
        f = m["file"]
        if m["class"] in TEXT and (BENCH / "crops" / f).exists() and gd.get(f, "").strip():
            items.append((f, BENCH / "crops" / f, m["class"], gd[f]))
    mant = {m["file"]: m for m in json.loads((BENCH / "manifest_test40.json").read_text(encoding="utf-8"))}
    for p in T_TEST.glob("*.txt"):
        f = p.stem + ".png"
        if f in mant and mant[f]["class"] in TEXT and (BENCH / "crops_test" / f).exists():
            items.append((f, BENCH / "crops_test" / f, mant[f]["class"], p.read_text(encoding="utf-8")))
    return items


# ── post-traitements ──
def pp_brut(s):
    return s or ""

def pp_cesures(s):
    return re.sub(r"([A-Za-zÀ-ÿ])[¬­\-]\s*\n\s*([a-zà-ÿ])", r"\1\2", s or "")

POSTPROC = {"brut": pp_brut, "césures": pp_cesures}


# ── métriques ──
def norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def cer(g, h):
    from rapidfuzz.distance import Levenshtein
    r = norm(g)
    return Levenshtein.distance(r, norm(h)) / len(r) if r else None

def wer(g, h):
    from rapidfuzz.distance import Levenshtein
    wg = norm(g).split()
    return Levenshtein.distance(wg, norm(h).split()) / len(wg) if wg else None
