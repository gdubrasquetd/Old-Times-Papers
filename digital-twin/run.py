"""Pipeline COMPLÈTE d'une une -> jumeau numérique.
Enchaîne : détection (YOLO) -> OCR (PERO-OCR, GPU) -> jumeau HTML.
Chaque étape tourne dans son env conda via subprocess.

    python run.py <slug|image.jpg> [--open]

Ex : python run.py le_temps_1936-08-08 --open
"""
import argparse, subprocess, sys, webbrowser
from pathlib import Path

# console Windows en cp1252 : évite les crashs d'affichage sur les accents/� de l'OCR
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ENVS = {
    "bloc_detection": r"C:/Users/antwi/.conda/envs/bloc_detection/python.exe",
    "oldspapers":     r"C:/Users/antwi/.conda/envs/oldspapers/python.exe",
    "ocr_torch":      r"C:/Users/antwi/.conda/envs/ocr_torch/python.exe",
    "pero":           r"C:/Users/antwi/.conda/envs/pero/python.exe",
}


def run(env, script, *args):
    cmd = [ENVS[env], str(HERE / script), *map(str, args)]
    print(f"\n$ [{env}] {script} {' '.join(map(str, args))}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        if line.strip() and "Warning" not in line and "it/s" not in line:
            print("   " + line)
    if r.returncode != 0:
        print("   STDERR:", (r.stderr or "")[-800:])
        sys.exit(f"échec de {script} ({env})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="slug (ex: le_temps_1936-08-08) ou chemin d'image")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    slug = Path(args.target).stem
    workdir = HERE / "out" / slug
    workdir.mkdir(parents=True, exist_ok=True)
    blocks = workdir / "blocks.json"
    twin = workdir / "twin.html"

    run("bloc_detection", "detect.py", args.target, blocks)
    run("pero", "ocr.py", blocks, "--stage", "pero")   # PERO : un seul moteur pour tout le texte
    run("oldspapers", "build.py", blocks, twin)        # PIL suffit

    print(f"\n✓ Jumeau prêt : {twin}")
    if args.open:
        webbrowser.open(twin.as_uri())


if __name__ == "__main__":
    main()
