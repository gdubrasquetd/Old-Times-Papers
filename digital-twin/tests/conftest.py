"""Ajoute digital-twin/ au sys.path pour que `import ocr` / `import build` marchent
depuis les tests. Ces modules n'importent que la stdlib + PIL au niveau module
(torch/pero/kraken/doctr sont importés paresseusement dans les runners), donc ils
se chargent sans modèle ni GPU."""
import sys
import pathlib

_DT = pathlib.Path(__file__).resolve().parents[1]
if str(_DT) not in sys.path:
    sys.path.insert(0, str(_DT))
