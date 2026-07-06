"""Compare doctr vs Kraken par classe : CER + WER. Lit kraken_all.json / doctr_all.json."""
import json, re, unicodedata
from pathlib import Path
from rapidfuzz.distance import Levenshtein

HERE = Path(__file__).resolve().parent
T_TEST = Path(r"C:/Users/antwi/AppData/Local/Temp/claude/C--Users-antwi-Projets-informatiques-Oldspapers/51357941-5bbb-4d20-8d15-9fa21a71f0c5/scratchpad/t_test")
TEXT = ["bloc de texte", "titre", "texte isolé"]


def norm(s):
    s = (s or "").replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip().lower()

def cer(g, h):
    r = norm(g); return Levenshtein.distance(r, norm(h)) / len(r) if r else None
def wer(g, h):
    wg = norm(g).split(); return Levenshtein.distance(wg, norm(h).split()) / max(1, len(wg)) if wg else None

# GT + classe pour chaque bloc
gt, cls = {}, {}
gd = json.loads((HERE / "gt.json").read_text(encoding="utf-8"))
for m in json.loads((HERE / "manifest.json").read_text(encoding="utf-8")):
    if m["class"] in TEXT and m["file"] in gd:
        gt[m["file"]] = gd[m["file"]]; cls[m["file"]] = m["class"]
mant = {m["file"]: m for m in json.loads((HERE / "manifest_test40.json").read_text(encoding="utf-8"))}
for p in T_TEST.glob("*.txt"):
    f = p.stem + ".png"
    if f in mant and mant[f]["class"] in TEXT:
        gt[f] = p.read_text(encoding="utf-8"); cls[f] = mant[f]["class"]

kr = json.loads((HERE / "kraken_all.json").read_text(encoding="utf-8"))
dc = json.loads((HERE / "doctr_all.json").read_text(encoding="utf-8"))


def stats(files, out):
    C = [cer(gt[f], out.get(f)) for f in files if out.get(f) is not None]
    W = [wer(gt[f], out.get(f)) for f in files if out.get(f) is not None]
    C = [x for x in C if x is not None]; W = [x for x in W if x is not None]
    return (sum(C)/len(C) if C else 0, sum(W)/len(W) if W else 0, len(C))

print(f"{'classe':<16}{'n':>4}   {'CER kraken':>11}{'CER doctr':>11}   {'WER kraken':>11}{'WER doctr':>11}")
for c in TEXT + ["TOUT"]:
    files = [f for f in gt if (cls[f] == c or c == "TOUT")]
    ck, wk, n = stats(files, kr); cd, wd, _ = stats(files, dc)
    star_c = " *" if cd < ck else ""
    star_w = " *" if wd < wk else ""
    print(f"{c:<16}{n:>4}   {ck:>10.1%}{cd:>10.1%}{star_c:<2} {wk:>10.1%}{wd:>10.1%}{star_w}")
print("\n(* = doctr meilleur. WER = taux d'erreur MOT ; c'est le critère 'bons mots'.)")

# blocs où kraken casse mais doctr non (WER)
print("\nBlocs où doctr sauve le plus (WER kraken -> doctr) :")
diffs = []
for f in gt:
    wk, wd = wer(gt[f], kr.get(f)), wer(gt[f], dc.get(f))
    if wk is not None and wd is not None:
        diffs.append((wk - wd, wk, wd, cls[f], f))
for d, wk, wd, c, f in sorted(diffs, reverse=True)[:8]:
    print(f"  {wk:5.1%} -> {wd:5.1%}  [{c:13}] {f[:44]}")
