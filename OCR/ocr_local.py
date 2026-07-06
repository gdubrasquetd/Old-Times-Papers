"""OCR local via PaddleOCR / Tesseract + layout analysis sur images IIIF Gallica."""
import io
import json
import pathlib
import re
import sys
import urllib.request
from collections import defaultdict

try:
    import numpy as _np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from PIL import Image, ImageEnhance
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# ── Tesseract ──────────────────────────────────────────────────────────────────
try:
    import pytesseract
    if not HAS_PIL:
        from PIL import Image, ImageEnhance  # noqa: F811
    if sys.platform == "win32":
        for _c in [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]:
            if pathlib.Path(_c).exists():
                pytesseract.pytesseract.tesseract_cmd = _c
                break
    pytesseract.get_tesseract_version()
    HAS_TESSERACT = True
except Exception:
    HAS_TESSERACT = False

# ── PaddleOCR (via sous-processus pour éviter le conflit DLL avec torch) ────────
# PaddlePaddle et PyTorch ne peuvent pas cohabiter dans le même processus Windows
# (DLL conflict sur shm.dll). On détecte la disponibilité de paddleocr sans l'importer.
_PADDLE_WORKER = pathlib.Path(__file__).parent / "paddle_worker.py"

def _check_paddle_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("paddleocr") is not None
    except Exception:
        return False

HAS_PADDLE = _check_paddle_available() and _PADDLE_WORKER.exists()

def _run_paddle_worker(img_path: pathlib.Path) -> dict:
    """Lance paddle_worker.py en sous-processus ; retourne le JSON parsé."""
    import subprocess
    python = pathlib.Path(sys.executable)
    result = subprocess.run(
        [str(python), str(_PADDLE_WORKER), str(img_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    stdout = (result.stdout or "").strip()
    if not stdout:
        stderr = (result.stderr or "")[-500:] or "(vide)"
        return {"error": f"paddle_worker sans sortie. stderr: {stderr}"}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        return {"error": f"JSON invalide depuis paddle_worker : {e}"}

# ── Surya ──────────────────────────────────────────────────────────────────────
try:
    from surya.layout import batch_layout_detection
    from surya.model.detection.model import load_model as _surya_load_model, load_processor as _surya_load_processor
    from surya.settings import settings as _surya_settings
    HAS_SURYA = True
except ImportError:
    HAS_SURYA = False

# Chargé une fois en mémoire lors du premier appel (modèle ~800 Mo)
_surya_model = None
_surya_processor = None

def _get_surya_model_processor():
    global _surya_model, _surya_processor
    if _surya_model is None:
        _surya_model = _surya_load_model(checkpoint=_surya_settings.LAYOUT_MODEL_CHECKPOINT)
        _surya_processor = _surya_load_processor(checkpoint=_surya_settings.LAYOUT_MODEL_CHECKPOINT)
    return _surya_model, _surya_processor

# Labels Surya considérés comme texte (→ OCR Tesseract)
# Compatible 0.6.13 (Title, Section-header) et versions antérieures (SectionHeader)
_TEXT_LABELS = frozenset({
    "Text", "Title", "Section-header", "SectionHeader",
    "Caption", "Footnote", "Bibliography",
    "PageHeader", "PageFooter", "Code",
})
# Couleurs par label (pour l'overlay frontend)
LABEL_COLORS = {
    "Title":          "#8b2c2c",   # rouge foncé (gros titres)
    "Section-header": "#b03a2e",   # rouge (sous-titres)
    "SectionHeader":  "#8b2c2c",
    "Text":           "#1a4a7a",   # bleu (corps)
    "Caption":        "#2d6a4f",   # vert (légendes)
    "Picture":        "#6a4f2d",   # brun (images)
    "Figure":         "#6a4f2d",
    "Table":          "#4f2d6a",   # violet
    "PageHeader":     "#555",
    "PageFooter":     "#555",
    "Footnote":       "#666",
}

# ── Kraken CATMuS-Print ────────────────────────────────────────────────────────
_KRAKEN_MODEL_PATH = pathlib.Path(
    r"C:\Users\antwi\AppData\Local\htrmopo\htrmopo"
    r"\d96caf7a-122e-5576-ab2b-a246c4e64221\catmus-print-fondue-large.mlmodel"
)
_kraken_net = None

try:
    from kraken import blla as _kraken_blla
    from kraken import rpred as _kraken_rpred
    from kraken.lib import models as _kraken_models
    from kraken.containers import (
        Segmentation as _KrakenSegmentation,
        BBoxLine as _KrakenBBoxLine,
    )
    HAS_KRAKEN = _KRAKEN_MODEL_PATH.exists()
except ImportError:
    HAS_KRAKEN = False

def _get_kraken_net():
    global _kraken_net
    if _kraken_net is None:
        _kraken_net = _kraken_models.load_any(str(_KRAKEN_MODEL_PATH))
    return _kraken_net


# ── Helpers ────────────────────────────────────────────────────────────────────

def _download_image_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _load_image(issue_ark: str, img_cache_dir: pathlib.Path | None) -> bytes:
    img_file = (img_cache_dir / f"{issue_ark}.jpg") if img_cache_dir else None
    if img_file and img_file.exists():
        return img_file.read_bytes()
    url = (f"https://gallica.bnf.fr/iiif/ark:/12148/{issue_ark}"
           f"/f1/full/full/0/native.jpg")
    return _download_image_bytes(url)


def _preprocess_gray(img: "Image.Image") -> "Image.Image":
    return ImageEnhance.Contrast(img.convert("L")).enhance(1.5)


# ── API publique ───────────────────────────────────────────────────────────────

def run_ocr_region(issue_ark: str, x0: float, y0: float, x1: float, y1: float,
                   img_cache_dir: pathlib.Path) -> dict:
    """OCR sur une zone dessinée par l'utilisateur (coordonnées normalisées 0-1).

    Retourne {"text": str, "engine": str} ou {"error": str}.
    """
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return {"error": "coordonnées invalides"}
    if not HAS_TESSERACT and not HAS_PADDLE:
        return {"error": "Aucun moteur OCR disponible (Tesseract ou PaddleOCR requis)"}

    # Charger l'image (cache 1500px)
    img_path = img_cache_dir / f"{issue_ark}.jpg"
    if img_path.exists():
        img = Image.open(img_path).convert("RGB")
    else:
        try:
            img_bytes = _download_image_bytes(
                f"https://gallica.bnf.fr/iiif/ark:/12148/{issue_ark}/f1/full/full/0/native.jpg"
            )
        except Exception as e:
            return {"error": f"Téléchargement échoué : {e}"}
        import io as _io
        img = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
        img_cache_dir.mkdir(parents=True, exist_ok=True)
        img.save(img_path, "JPEG", quality=92)

    w, h = img.size
    px0, py0 = int(x0 * w), int(y0 * h)
    px1, py1 = int(x1 * w), int(y1 * h)

    # Marge légère pour ne pas couper les bords des lettres
    margin = 4
    crop = img.crop((max(0, px0 - margin), max(0, py0 - margin),
                     min(w, px1 + margin), min(h, py1 + margin)))

    # Upscale si la zone est petite → meilleure reconnaissance
    cw, ch = crop.size
    scale = 1
    if cw < 600:
        scale = max(2, min(4, 1200 // max(cw, 1)))
    if scale > 1:
        crop = crop.resize((cw * scale, ch * scale), Image.LANCZOS)

    # Prétraitement : niveaux de gris + contraste
    crop_gray = ImageEnhance.Contrast(crop.convert("L")).enhance(1.6)

    if HAS_TESSERACT:
        try:
            text = pytesseract.image_to_string(
                crop_gray, lang="fra",
                config="--psm 6 --oem 3"
            ).strip()
            if text:
                return {"text": text, "engine": "tesseract"}
        except Exception:
            pass

    if HAS_PADDLE:
        import tempfile, subprocess as _sp
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            crop_gray.save(tmp.name)
            tmp_path = pathlib.Path(tmp.name)
        try:
            worker_result = _run_paddle_worker(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        if "lines" in worker_result:
            text = " ".join(l["text"] for l in worker_result["lines"])
            return {"text": text, "engine": "paddle"}
        return worker_result

    return {"error": "OCR échoué"}


def run_ocr_region_kraken(issue_ark: str, x0: float, y0: float, x1: float, y1: float,
                           img_cache_dir: pathlib.Path) -> dict:
    """OCR Kraken CATMuS-Print sur une zone normalisée (0-1).

    Utilise blla pour segmenter les lignes dans le crop, puis rpred pour
    reconnaître chaque ligne. Fallback BBox si blla ne trouve rien.
    """
    if not HAS_KRAKEN:
        return {"error": "Kraken non disponible (kraken non installé ou modèle introuvable)"}
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return {"error": "coordonnées invalides"}

    img_path = img_cache_dir / f"{issue_ark}.jpg"
    if img_path.exists():
        img = Image.open(img_path).convert("RGB")
    else:
        try:
            img_bytes = _download_image_bytes(
                f"https://gallica.bnf.fr/iiif/ark:/12148/{issue_ark}/f1/full/full/0/native.jpg"
            )
        except Exception as e:
            return {"error": f"Téléchargement échoué : {e}"}
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_cache_dir.mkdir(parents=True, exist_ok=True)
        img.save(img_path, "JPEG", quality=92)

    w, h = img.size
    margin = 4
    px0 = max(0, int(x0 * w) - margin)
    py0 = max(0, int(y0 * h) - margin)
    px1 = min(w, int(x1 * w) + margin)
    py1 = min(h, int(y1 * h) + margin)
    crop = img.crop((px0, py0, px1, py1))

    cw, ch = crop.size
    if cw < 10 or ch < 4:
        return {"error": "Zone trop petite"}

    # Upscale si hauteur < 80px pour aider la reconnaissance
    if ch < 80:
        scale = max(2, min(4, 160 // max(ch, 1)))
        crop = crop.resize((cw * scale, ch * scale), Image.LANCZOS)

    try:
        net = _get_kraken_net()

        # Segmentation neurale des lignes dans le crop (blla)
        seg = _kraken_blla.segment(crop, device="cpu")
        lines_text = []

        if seg.lines:
            for record in _kraken_rpred.rpred(net, crop, seg):
                if record.prediction.strip():
                    lines_text.append(record.prediction)

        if not lines_text:
            # Fallback : tout le crop = une seule ligne BBox
            line_box = _KrakenBBoxLine(
                id="l0", bbox=(0, 0, crop.width, crop.height), text=None
            )
            seg_bb = _KrakenSegmentation(
                type="bbox", imagename="",
                text_direction="horizontal-lr",
                script_detection=False,
                lines=[line_box], regions={}, line_orders=[],
            )
            for record in _kraken_rpred.rpred(net, crop, seg_bb):
                if record.prediction.strip():
                    lines_text.append(record.prediction)
                break

        if not lines_text:
            return {"error": "Aucun texte extrait"}

        return {"text": "\n".join(lines_text), "engine": "kraken", "n_lines": len(lines_text)}

    except Exception as e:
        return {"error": f"Kraken : {e}"}


def run_ocr_full_kraken(issue_ark: str, cache_dir: pathlib.Path,
                         img_cache_dir: pathlib.Path | None = None) -> dict:
    """OCR Kraken CATMuS-Print pleine page avec segmentation blla.

    Cache le résultat dans cache_dir/{issue_ark}_kraken.txt.
    """
    if not HAS_KRAKEN:
        return {"error": "Kraken non disponible (kraken non installé ou modèle introuvable)"}
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}

    text_file = cache_dir / f"{issue_ark}_kraken.txt"
    if text_file.exists():
        text = text_file.read_text(encoding="utf-8", errors="replace")
        n = text.count("\n") + 1 if text.strip() else 0
        return {"text": text, "cached": True, "n_lines": n, "engine": "kraken"}

    img_path = (img_cache_dir / f"{issue_ark}.jpg") if img_cache_dir else None
    if img_path and img_path.exists():
        img = Image.open(img_path).convert("RGB")
    else:
        try:
            img_bytes = _download_image_bytes(
                f"https://gallica.bnf.fr/iiif/ark:/12148/{issue_ark}/f1/full/full/0/native.jpg"
            )
        except Exception as e:
            return {"error": f"Téléchargement échoué : {e}"}
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        if img_path:
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(img_path, "JPEG", quality=92)

    try:
        net = _get_kraken_net()
        seg = _kraken_blla.segment(img, device="cpu")
        lines_text = []
        if seg.lines:
            for record in _kraken_rpred.rpred(net, img, seg):
                if record.prediction.strip():
                    lines_text.append(record.prediction)
        if not lines_text:
            return {"error": "Aucun texte extrait (image trop dégradée ?)"}
        text = "\n".join(lines_text)
        cache_dir.mkdir(parents=True, exist_ok=True)
        text_file.write_text(text, encoding="utf-8")
        return {"text": text, "cached": False, "n_lines": len(lines_text), "engine": "kraken"}
    except Exception as e:
        return {"error": f"Kraken : {e}"}


def download_image(issue_ark: str, img_cache_dir: pathlib.Path) -> dict:
    """Télécharge l'image IIIF 1500px et la met en cache disque."""
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}
    img_file = img_cache_dir / f"{issue_ark}.jpg"
    if img_file.exists():
        return {"ok": True, "cached": True}
    url = (f"https://gallica.bnf.fr/iiif/ark:/12148/{issue_ark}"
           f"/f1/full/full/0/native.jpg")
    try:
        img_bytes = _download_image_bytes(url)
    except Exception as e:
        return {"error": f"Téléchargement échoué : {e}"}
    img_cache_dir.mkdir(parents=True, exist_ok=True)
    img_file.write_bytes(img_bytes)
    return {"ok": True, "cached": False}


def run_ocr(issue_ark: str, cache_dir: pathlib.Path,
            img_cache_dir: pathlib.Path | None = None) -> dict:
    """OCR pleine page Tesseract (texte brut)."""
    if not HAS_TESSERACT:
        return {"error": "Tesseract non installé"}
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}
    text_file = cache_dir / f"{issue_ark}.txt"
    if text_file.exists():
        text = text_file.read_text(encoding="utf-8", errors="replace")
        return {"text": text, "cached": True, "length": len(text)}
    try:
        img_bytes = _load_image(issue_ark, img_cache_dir)
    except Exception as e:
        return {"error": f"Téléchargement échoué : {e}"}
    img = _preprocess_gray(Image.open(io.BytesIO(img_bytes)))
    try:
        text = pytesseract.image_to_string(img, lang="fra", config="--psm 1 --oem 3")
    except pytesseract.TesseractError as e:
        return {"error": f"Tesseract : {e}"}
    text = text.strip()
    if not text:
        return {"error": "Aucun texte extrait (image trop dégradée ?)"}
    text_file.write_text(text, encoding="utf-8")
    return {"text": text, "cached": False, "length": len(text)}


def _find_col_boundaries(word_xcs, img_w, bin_px=15, smooth_k=5, min_peak_ratio=0.12):
    """
    Détecte les frontières de colonnes à partir des centres x des mots étroits.
    Renvoie une liste triée de x-positions [0, sep1, sep2, ..., img_w].
    """
    if not word_xcs:
        return [0, img_w]
    hist, edges = _np.histogram(word_xcs, bins=img_w // bin_px, range=(0, img_w))
    # Lissage
    k = _np.ones(smooth_k) / smooth_k
    smooth = _np.convolve(hist.astype(float), k, "same")
    threshold = smooth.max() * min_peak_ratio

    # Trouver les pics (centres de colonnes)
    peaks = []
    for i in range(1, len(smooth) - 1):
        if smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1] and smooth[i] > threshold:
            peaks.append(i)
    # Fusionner les pics adjacents (dans la même colonne)
    merged = []
    for p in peaks:
        if merged and p - merged[-1] <= smooth_k:
            merged[-1] = (merged[-1] + p) // 2
        else:
            merged.append(p)

    if len(merged) < 2:
        return [0, img_w]

    # Frontières = milieux entre pics consécutifs
    bounds = [0]
    for i in range(len(merged) - 1):
        mid = int((edges[merged[i]] + edges[merged[i + 1]]) / 2 + bin_px / 2)
        bounds.append(mid)
    bounds.append(img_w)
    return bounds


def run_layout_blocks(issue_ark: str, cache_dir: pathlib.Path,
                      img_cache_dir: pathlib.Path | None = None) -> dict:
    """Dispatcher : PaddleOCR si disponible, sinon Tesseract word-level."""
    if HAS_PADDLE and HAS_NUMPY:
        return _run_layout_blocks_paddle(issue_ark, cache_dir, img_cache_dir)
    return _run_layout_blocks_tesseract(issue_ark, cache_dir, img_cache_dir)


def _run_layout_blocks_paddle(issue_ark: str, cache_dir: pathlib.Path,
                               img_cache_dir: pathlib.Path | None = None) -> dict:
    """Layout analysis via PaddleOCR : lignes détectées + groupement par colonnes."""
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}

    cache_file = cache_dir / f"{issue_ark}_layout_paddle.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        data["cached"] = True
        return data

    # Image : chemin disque requis par PaddleOCR
    img_path = (img_cache_dir / f"{issue_ark}.jpg") if img_cache_dir else None
    if img_path is None or not img_path.exists():
        try:
            img_bytes = _load_image(issue_ark, img_cache_dir)
        except Exception as e:
            return {"error": f"Téléchargement échoué : {e}"}
        if img_path:
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(img_bytes)
        else:
            # Pas de cache → fichier temporaire
            import tempfile, os
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(img_bytes)
            tmp.close()
            img_path = pathlib.Path(tmp.name)

    img_rgb = Image.open(img_path).convert("RGB")
    img_w, img_h = img_rgb.size

    # ── 1. PaddleOCR → lignes texte (via sous-processus paddle_worker.py) ────────
    worker_result = _run_paddle_worker(img_path)
    if "error" in worker_result:
        return worker_result

    lines = worker_result.get("lines", [])

    if not lines:
        return {"error": "Aucune ligne avec confiance suffisante"}

    # ── 2. Détection colonnes depuis les lignes étroites ─────────────────────────
    max_line_w = img_w // 5   # < 20 % → corps de texte d'une colonne
    narrow_xcs = [l["xc"] for l in lines if l["w"] < max_line_w]
    col_bounds = _find_col_boundaries(narrow_xcs, img_w)
    n_cols = len(col_bounds) - 1

    # ── 3. Assignation à la colonne ───────────────────────────────────────────────
    def col_of(xc):
        for j in range(n_cols):
            if col_bounds[j] <= xc < col_bounds[j + 1]:
                return j
        return n_cols - 1

    for l in lines:
        l["col"] = col_of(l["xc"])

    # ── 4. Groupement vertical + classification ──────────────────────────────────
    blocks = []
    for col_i in range(n_cols):
        col_lines = sorted(
            [l for l in lines if l["col"] == col_i],
            key=lambda l: (l["y0"], l["x0"]),
        )
        if not col_lines:
            continue
        hs = sorted(l["h"] for l in col_lines if 3 < l["h"] < 200)
        med_h = hs[len(hs) // 2] if hs else 14
        # Lignes PaddleOCR = multi-mots → inter-ligne ~2-5px, inter-article ~10-25px.
        # 0.8× la hauteur médiane capture les séparations inter-articles.
        gap_thresh = max(med_h * 0.8, 6)
        cx0, cx1 = col_bounds[col_i], col_bounds[col_i + 1]

        groups = [[col_lines[0]]]
        for l in col_lines[1:]:
            gap = l["y0"] - groups[-1][-1]["y1"]
            if gap > gap_thresh:
                groups.append([l])
            else:
                groups[-1].append(l)

        for grp in groups:
            text = " ".join(l["text"] for l in grp)
            if len(text.replace(" ", "")) < 4:
                continue
            by0 = min(l["y0"] for l in grp)
            by1 = max(l["y1"] for l in grp)
            # Filtrer les micro-blocs : 1 seule ligne + hauteur < 1.5% page
            # → numérotations, bandeaux, artefacts typographiques
            if len(grp) == 1 and (by1 - by0) / img_h < 0.015:
                continue
            g_hs = [l["h"] for l in grp if 3 < l["h"] < 200]
            avg_h = sum(g_hs) / len(g_hs) if g_hs else 0
            # Seuil titre abaissé à 1.4× (1.8 était trop restrictif)
            label = "Title" if avg_h > med_h * 1.4 else "Text"
            blocks.append({
                "label":      label,
                "x0":         round(cx0 / img_w, 4),
                "y0":         round(by0 / img_h, 4),
                "x1":         round(cx1 / img_w, 4),
                "y1":         round(by1 / img_h, 4),
                "position":   len(blocks),
                "confidence": round(sum(l["conf"] for l in grp) / len(grp), 3),
                "color":      LABEL_COLORS.get(label, "#888"),
                "text":       text,
            })

    blocks.sort(key=lambda b: (round(b["x0"] * n_cols), round(b["y0"] * 20)))
    for i, b in enumerate(blocks):
        b["position"] = i

    if not blocks:
        return {"error": "Aucun bloc constitué"}

    result = {"blocks": blocks, "img_w": img_w, "img_h": img_h,
              "n_cols": n_cols, "engine": "paddle"}
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    result["cached"] = False
    return result


def _run_layout_blocks_tesseract(issue_ark: str, cache_dir: pathlib.Path,
                                  img_cache_dir: pathlib.Path | None = None) -> dict:
    """Layout analysis Tesseract word-level (fallback si PaddleOCR absent).

    Algorithme :
      1. Tesseract image_to_data → mots individuels avec positions
      2. Colonnes détectées depuis les mots étroits (corps de texte)
         via histogramme des centres x → pics = centres de colonnes
      3. Chaque mot est assigné à la colonne la plus proche
      4. Dans chaque colonne, groupement vertical par écart entre lignes
         (nouveau bloc quand gap > 2,5 × hauteur médiane des caractères)
      5. Classification Title / Text selon hauteur relative des caractères
      6. x0/x1 des blocs = bornes de la colonne (pas des mots) → overlay propre

    Retourne {"blocks": [...], "img_w", "img_h", "cached"} ou {"error": str}.
    """
    if not HAS_TESSERACT:
        return {"error": "Tesseract non installé"}
    if not HAS_NUMPY:
        return {"error": "numpy non installé"}
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}

    cache_file = cache_dir / f"{issue_ark}_layout.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        data["cached"] = True
        return data

    try:
        img_bytes = _load_image(issue_ark, img_cache_dir)
    except Exception as e:
        return {"error": f"Téléchargement échoué : {e}"}

    img_rgb = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    img_w, img_h = img_rgb.size
    img_gray = _preprocess_gray(img_rgb)

    # ── 1. Tesseract → mots individuels ─────────────────────────────────────────
    try:
        tsv = pytesseract.image_to_data(
            img_gray, lang="fra", config="--psm 3 --oem 3",
            output_type=pytesseract.Output.DICT,
        )
    except Exception as e:
        return {"error": f"Tesseract : {e}"}

    words = []
    for i in range(len(tsv["text"])):
        txt = tsv["text"][i].strip()
        b   = tsv["block_num"][i]
        if b == 0 or int(tsv["conf"][i]) < 15 or not txt:
            continue
        l, t, w, h = tsv["left"][i], tsv["top"][i], tsv["width"][i], tsv["height"][i]
        if h <= 0 or w <= 0:
            continue
        words.append({
            "t": txt, "h": h, "w": w,
            "x0": l, "y0": t, "x1": l + w, "y1": t + h,
            "xc": l + w // 2,
        })

    if not words:
        return {"error": "Aucun mot détecté"}

    # ── 2. Détection des colonnes depuis les mots étroits ───────────────────────
    # Les mots larges sont souvent des titres qui franchissent les colonnes.
    # On détecte les colonnes uniquement depuis les mots de corps de texte.
    max_word_w = img_w // 7     # ≈ 214 px pour 1500 px
    narrow_xcs = [w["xc"] for w in words if w["w"] < max_word_w]

    col_bounds = _find_col_boundaries(narrow_xcs, img_w)
    n_cols = len(col_bounds) - 1

    # ── 3. Assignation de chaque mot à sa colonne ────────────────────────────────
    def col_of(xc):
        for j in range(n_cols):
            if col_bounds[j] <= xc < col_bounds[j + 1]:
                return j
        return n_cols - 1

    for w in words:
        w["col"] = col_of(w["xc"])

    # ── 4. Groupement vertical dans chaque colonne ───────────────────────────────
    blocks = []
    for col_i in range(n_cols):
        col_words = sorted(
            [w for w in words if w["col"] == col_i],
            key=lambda w: (w["y0"], w["x0"]),
        )
        if not col_words:
            continue

        # Hauteur médiane des mots de cette colonne (corps de texte)
        hs = sorted(w["h"] for w in col_words if 3 < w["h"] < 200)
        med_h = hs[len(hs) // 2] if hs else 12
        gap_thresh = max(med_h * 2.5, 12)   # gap > 2,5 lignes → nouvel article

        cx0 = col_bounds[col_i]
        cx1 = col_bounds[col_i + 1]

        # Découpage en blocs par gap vertical
        groups: list = [[col_words[0]]]
        for w in col_words[1:]:
            last = groups[-1][-1]
            gap  = w["y0"] - last["y1"]
            if gap > gap_thresh:
                groups.append([w])
            else:
                groups[-1].append(w)

        for grp in groups:
            text = " ".join(w["t"] for w in grp)
            if len(text.replace(" ", "")) < 6:
                continue
            by0 = min(w["y0"] for w in grp)
            by1 = max(w["y1"] for w in grp)
            g_hs = [w["h"] for w in grp if 3 < w["h"] < 200]
            avg_h = sum(g_hs) / len(g_hs) if g_hs else 0
            # Titre si hauteur moyenne > 2× la médiane de la colonne
            label = "Title" if avg_h > med_h * 2.0 else "Text"
            blocks.append({
                "label":      label,
                "x0":         round(cx0 / img_w, 4),
                "y0":         round(by0 / img_h, 4),
                "x1":         round(cx1 / img_w, 4),
                "y1":         round(by1 / img_h, 4),
                "position":   len(blocks),
                "confidence": 1.0,
                "color":      LABEL_COLORS.get(label, "#888"),
                "text":       text,
            })

    # ── 5. Tri ordre de lecture ───────────────────────────────────────────────────
    blocks.sort(key=lambda b: (round(b["x0"] * n_cols), round(b["y0"] * 20)))
    for i, b in enumerate(blocks):
        b["position"] = i

    if not blocks:
        return {"error": "Aucun bloc détecté"}

    result = {"blocks": blocks, "img_w": img_w, "img_h": img_h, "n_cols": n_cols}
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    result["cached"] = False
    return result


# Ancien moteur TSV conservé pour comparaison / fallback
def run_ocr_blocks(issue_ark: str, cache_dir: pathlib.Path,
                   img_cache_dir: pathlib.Path | None = None) -> dict:
    """Détection de blocs Tesseract TSV (fallback si Surya absent)."""
    if not HAS_TESSERACT:
        return {"error": "Tesseract non installé"}
    if not re.fullmatch(r"(?:bpt6k|bd6t|btv1b)[a-z0-9]+", issue_ark, re.IGNORECASE):
        return {"error": "ark invalide"}
    blocks_file = cache_dir / f"{issue_ark}_blocks.json"
    if blocks_file.exists():
        data = json.loads(blocks_file.read_text(encoding="utf-8"))
        data["cached"] = True
        return data
    try:
        img_bytes = _load_image(issue_ark, img_cache_dir)
    except Exception as e:
        return {"error": f"Téléchargement échoué : {e}"}
    img = _preprocess_gray(Image.open(io.BytesIO(img_bytes)))
    img_w, img_h = img.size
    try:
        tsv = pytesseract.image_to_data(
            img, lang="fra", config="--psm 1 --oem 3",
            output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractError as e:
        return {"error": f"Tesseract : {e}"}
    groups: dict = defaultdict(lambda: {
        "words": [], "lines": defaultdict(list),
        "x0": float("inf"), "y0": float("inf"), "x1": 0, "y1": 0,
    })
    n = len(tsv["block_num"])
    for i in range(n):
        b, p, ln = tsv["block_num"][i], tsv["par_num"][i], tsv["line_num"][i]
        if b == 0 or int(tsv["conf"][i]) < 0 or not tsv["text"][i].strip():
            continue
        left, top, w, h = tsv["left"][i], tsv["top"][i], tsv["width"][i], tsv["height"][i]
        g = groups[(b, p)]
        g["x0"] = min(g["x0"], left); g["y0"] = min(g["y0"], top)
        g["x1"] = max(g["x1"], left + w); g["y1"] = max(g["y1"], top + h)
        g["lines"][ln].append(tsv["text"][i])
    blocks = []
    for g in sorted(groups.values(), key=lambda g: (round(g["y0"] * 10 / img_h), g["x0"])):
        if g["x0"] == float("inf"):
            continue
        text = "\n".join(" ".join(ws) for _, ws in sorted(g["lines"].items())).strip()
        if text:
            blocks.append({
                "label": "Text", "color": "#1a4a7a",
                "x0": round(g["x0"] / img_w, 4), "y0": round(g["y0"] / img_h, 4),
                "x1": round(g["x1"] / img_w, 4), "y1": round(g["y1"] / img_h, 4),
                "text": text,
            })
    if not blocks:
        return {"error": "Aucun bloc détecté"}
    result = {"blocks": blocks, "img_w": img_w, "img_h": img_h}
    blocks_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    result["cached"] = False
    return result
