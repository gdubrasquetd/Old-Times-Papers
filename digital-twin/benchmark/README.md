# Récapitulatif — Banc d'essai OCR sur les unes de presse ancienne

Ce document résume tout le travail de comparaison et de calibration des moteurs
OCR, jusqu'à la **politique de routage figée** utilisée par la pipeline.

Voir aussi : `../bloc_detection/RECAP.md` (détection de blocs, l'étape amont).

---

## 1. Objectif

Choisir, pour chaque **type de bloc** d'une une, le moteur OCR qui lit le mieux
l'imprimé français ancien — sur GPU modeste (RTX 3050, 4 Go), 100 % local et gratuit.

## 2. Protocole du banc

1. `make_crops.py` : YOLO v3 (`multiclass_yolo11s_v3`) détecte les blocs d'une une
   et sauve un crop par bloc (`crops/`, `manifest.json`).
2. `ocr_bench.py` : chaque moteur OCRise les crops → `results.json` (fusionné par moteur).
3. `gt_app.py` : outil web (port 5056) pour saisir/corriger la **vérité terrain**
   au mot (overlay Tesseract éditable) → `gt.json`.
4. `cer.py` : **CER** (Character Error Rate = distance de Levenshtein / longueur GT,
   texte normalisé NFC/minuscule/espaces) de chaque moteur. Plus bas = mieux.

## 3. Moteurs testés (et leur env conda)

| moteur | env | verdict |
|---|---|---|
| **Kraken** (CATMuS-Print-fondue-large) | `oldspapers` | **meilleur sur le corps** (~3 %) ; échoue sur titres/display |
| **doctr** | `ocr_torch` | régulier sur titres & texte isolé (~5–12 %) |
| **easyocr** | `bloc_detection` | correct mais **instable** (un titre à 88 %) |
| **tesseract** (binaire) | tout | baseline ; sensible au `--psm` |
| surya / paddle | oldspapers / ocr_paddle | abandonnés (API cassées, bug oneDNN) |

## 4. Vérité terrain

- GT saisie **par transcription soignée** (diplomatique : orthographe d'époque,
  césures conservées). Les crops hauts sont découpés en bandes agrandies pour lire.
- ⚠️ **Caveat** : la GT est notre propre lecture, pas un étalon indépendant. Un
  moteur qui partage nos erreurs serait sous-pénalisé. Fiable mais à garder en tête.

## 5. Prétraitement (pour l'aide Tesseract de `gt_app.py`)

- **Binarisation adaptative** (gaussienne) : gère le fond inégal du papier ancien.
- **Despeckle par composantes connexes** (`DESPECKLE_MIN_AREA = 15` px) : retire les
  micro-taches **sans éroder** les glyphes ni les accents.
- **PAS d'ouverture/fermeture morphologique** : testé, ça amincit/soude les lettres
  fines → dégrade. (Comparaisons visuelles à l'appui.)
- **Filtrage par confiance** de la sortie Tesseract : jette les tokens à basse conf.
- Sur les **titres**, au contraire, le prétraitement **dégrade** Tesseract (garder brut,
  `--psm 3` au lieu de 6).

## 6. Résultats

### Dev (2 unes : Le Figaro 1937, L'Intransigeant 1909)
CER moyen sur les blocs de corps substantiels : **Kraken 2.5 %**, doctr 6.1 %,
easyocr 6.6 %, tesseract 9.4 %.

### Découverte clé : aucun moteur unique ne gagne partout
- **Corps de texte** : Kraken domine (segmentation `blla` + reco CATMuS).
- **Titres / mastheads / texte isolé** : Kraken `blla` **s'effondre** (96–1911 %) car
  `blla` est fait pour des pages à colonnes, pas pour du gros display type.
  → doctr (détection propre) est bien plus robuste.
- Les **mastheads** en gothique/multi-lignes restent durs pour tout OCR.

### Test de généralisation (8 unes INÉDITES, 1902→1935, 36+3 blocs)
Pipeline **figée** (aucun re-tuning) :

| classe | route | CER micro (test) |
|---|---|---|
| bloc de texte | Kraken | **3.15 %** |
| titre | doctr | **5.15 %** |
| texte isolé | doctr | **~10 %** |
| **contenu global** | | **micro 3.4 % / macro 5.2 %** |

→ La chaîne **généralise** : ~3.4 % d'erreur caractère sur le contenu utile, sur des
journaux jamais vus. Le ~3 % du dev n'était pas du sur-apprentissage.

### Deux pièges attrapés par le test (c'est son intérêt)
1. **Headers en `kraken_whole`** : marchait sur les 2 mastheads du dev (0 %), généralisait
   mal (27–31 %). Sans conséquence : le header = nom du journal, connu d'avance → **pas d'OCR**.
2. **`texte isolé` routé vers Kraken** : 75 % d'erreur ! C'est du display type → **doctr** (12 %).
   Invisible tant qu'aucun `texte isolé` n'était échantillonné.

### Le post-traitement CLÉ : recollage des césures

Le CER (~3 %) cachait un **WER de 14 %**. En inspectant, quasi **toutes** les fautes de
mots sur les blocs propres étaient des **coupures de fin de ligne** non recollées :
`l'auto¬\nrité` compté comme 2 mots faux alors que Kraken avait lu le mot parfaitement.

→ Post-traitement `dehyphenate()` (recolle `mot-\nsuite` -> `motsuite` ; les tirets réels
de composés restent en milieu de ligne). Effet sur le **corps** :

| corps | CER | WER |
|---|---|---|
| sans recollage | 3.3 % | 14.4 % |
| **avec recollage** | **2.3 %** | **8.6 %** |
| **années 1930 (propre)** | **1.6 %** | **5.2 %** |
| Le Temps 1934 seul | — | **3.1 %** |

Conclusion : sur l'imprimé propre des années 30 pré-découpé, on atteint la cible SOTA
(**~3-6 % WER**). Le « problème OCR » était surtout un **problème de reformatage**.
`dehyphenate()` est désormais appliqué systématiquement dans `../twin/twin_ocr.py`.
Métriques mot/phrase : `eval_metrics.py` (le CER seul est trompeur, toujours regarder le WER).

## 7. POLITIQUE FIGÉE — PERO-OCR (moteur unique)

Après le **grand comparatif** (§10), **PERO-OCR bat tous les autres sur toutes les
classes** et remplace le routage Kraken/doctr :

| classe de bloc (YOLO) | traitement | env |
|---|---|---|
| `bloc de texte` / `titre` / `texte isolé` | **PERO-OCR** (modèle presse EU) + recollage césures | `pero` |
| `header` | **pas d'OCR** → nom du journal connu | — |
| `illustration` | ignoré | — |

PERO fait sa **propre détection lignes+régions** (robuste) → pas de casse de segmentation,
un seul moteur pour tout (~4 % WER global, 0.9 % CER). Modèle :
`OCR/bench/comp/models/pero/pero_eu_cz_print_newspapers_2022-09-26/` (config_cpu.ini).
Kraken (CATMuS) + doctr restent en secours dans `twin_ocr.py`.

## 8. Fichiers clés (`OCR/bench/`)

| fichier | rôle |
|---|---|
| `make_crops.py` / `make_crops_test.py` | dev / test : une → crops de blocs |
| `ocr_bench.py` | OCR multi-moteurs → results.json |
| `gt_app.py` | outil de vérité terrain au mot (port 5056) |
| `cer.py` | CER par moteur |
| `pipeline_run.py` | **exécute la politique figée** (2 étapes : kraken, doctr) |
| `eval_test.py` / `eval_dev_isole.py` | évaluation du test / des texte isolé |
| `problem_report.py` | rapport HTML des cas problématiques |

## 9. Limites connues / à faire

- GT = notre lecture (cf. §4).
- Petit échantillon (10 journaux au total). Élargir renforcerait la confiance.
- Le Petit Parisien (petit corps flou) : GT de corps écartée par prudence.
- Kraken lent (~30–77 s/bloc CPU). Acceptable hors temps réel.

## 10. GRAND COMPARATIF (4 juillet 2026) — dossier `comp/`

53 blocs (dev+test), 8+ moteurs × configs × 2 post-traitements (brut / recollage césures).
WER global, meilleur post-traitement :

| moteur+config | WER corps | WER titre | WER isolé | WER global | CER global |
|---|---|---|---|---|---|
| **PERO** (presse EU) | **3.8 %** | **17 %** | **11 %** | **4.1 %** | **0.9 %** |
| Kraken blla | 7.3 % | 99 % | 94 % | 10.3 % | 4.5 % |
| doctr | 17.5 % | 37 % | 37 % | 18.2 % | 4.6 % |
| Tesseract psm3 brut | 27.6 % | 33 % | 47 % | 28.1 % | 6.8 % |
| easyocr paragraph | 30.9 % | 38 % | 32 % | 31.0 % | 7.4 % |
| Tesseract binarisé | 75 % | 76 % | 77 % | 75 % | 35 % |
| Calamari `historical_french` | 82 % | 88 % | — | 82 % | 70 % |

**Conclusions :**
- **PERO gagne partout** (détection lignes/régions intégrée → robuste sur titres/isolé aussi).
  → moteur unique, fin du routage et des blocs cassés.
- **Recollage des césures** : -5 % WER sur tous les moteurs.
- **Binariser tue Tesseract** (28 → 75 %). easyocr : `paragraph=True` indispensable (31 vs 59 %).
- **Calamari `historical_french` = early-modern** (sort des « ſ » longs) → inadapté à 1850-1950.
  Leçon : « modèle français historique » ≠ notre époque ; CATMuS et PERO-presse la couvrent.

Harnais : `comp/eval_lib.py`, `comp/run_*.py` (un par moteur/env), `comp/comprehensive_eval.py`
-> `comp/comparison.html`. Modèles : `comp/models/pero/`, `comp/models/calamari_repo/`.
