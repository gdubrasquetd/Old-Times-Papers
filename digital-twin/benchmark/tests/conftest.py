"""Ajoute benchmark/comp/ au sys.path pour `import eval_lib`. eval_lib n'importe
que la stdlib au niveau module (rapidfuzz est chargé paresseusement dans cer/wer),
donc il se charge sans dépendance lourde."""
import sys
import pathlib

_COMP = pathlib.Path(__file__).resolve().parents[1] / "comp"
if str(_COMP) not in sys.path:
    sys.path.insert(0, str(_COMP))
