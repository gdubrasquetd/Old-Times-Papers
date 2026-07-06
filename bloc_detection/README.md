# bloc_detection — Détection de blocs sur les unes (Document Layout Analysis)

Entraîne un modèle de détection de blocs sur les unes de presse historique annotées
dans `annotation_tool/`. Objectif : détecter 6 types de blocs sous forme de boîtes
englobantes, en vue de découper chaque une en zones avant OCR.

## Classes (6)

`header`, `titre`, `illustration`, `bloc de texte`, `texte isolé`, `autres`
(ordre = `id` dans la table `labels` de `annotation_tool/data/annotations.db`)

## Contraintes matérielles

GPU = **RTX 3050 Laptop, 4 Go VRAM**. Conséquences :
- modèles légers (`yolo11s`/`yolo11n`, `rtdetr-l` en dernier recours)
- batch petit (1-4), `imgsz` ≤ 1280, AMP activé
- RT-DETR plus gourmand → batch 1-2, baisser `imgsz` si OOM

## Approche : comparer YOLO11 vs RT-DETR

Les deux sont dans Ultralytics → **même format de données, même API**. Le pipeline
d'export est partagé ; seul le modèle change à l'entraînement. On compare la mAP par
classe pour trancher.

## Phases

- **Phase 0 — tuyauterie (en cours)** : `export_dataset.py` (DB → format YOLO),
  squelettes `train.py` / `infer.py`. Validé de bout en bout même sur peu d'images.
- **Phase 1 — baseline** : premier vrai entraînement vers 50-80 unes annotées,
  analyse mAP par classe (déséquilibre `autres`/`illustration` à surveiller).
- **Phase 2 — annotation assistée** : le modèle pré-remplit les boîtes dans
  l'outil d'annotation ; on corrige au lieu de tout dessiner. Gros gain de temps.
- **Phase 3 — optimisation** : résolution, tiling, rééquilibrage classes, gel v1.
- **Phase 4 — intégration** : dans Oldspapers (détection blocs → OCR par bloc),
  rejoint le plan OCR local.

## Workflow

```powershell
conda env create -f bloc_detection/environment.yml   # 1re fois
conda activate bloc_detection

# 1. Exporter le dataset depuis la DB d'annotation (images 'done')
python bloc_detection/export_dataset.py

# 2. Entraîner (comparer les deux modèles)
python bloc_detection/train.py --model yolo11s --imgsz 1280
python bloc_detection/train.py --model rtdetr-l --imgsz 1024 --batch 2

# 3. Inférer / visualiser sur une image
python bloc_detection/infer.py --weights runs/yolo11s/weights/best.pt --image <chemin.jpg>
```

## Annotation assistée (Phase 2, déjà en place)

Un détecteur **mono-classe** (`bloc`) entraîné sur peu d'unes suffit déjà à
pré-proposer le découpage. Le script écrit ses propositions dans la base de
l'outil d'annotation ; l'UI les matérialise en boîtes éditables.

```powershell
# a) entraîner le détecteur de blocs (1 classe)
python bloc_detection/export_dataset.py --single-class      # -> dataset_blocs/
python bloc_detection/train.py --data bloc_detection/dataset_blocs/data.yaml \
       --model yolo11s --imgsz 1280 --batch 2 --epochs 200 --patience 50 --name blocs_yolo11s

# b) pré-calculer les suggestions sur les unes à annoter (écrit en DB)
python bloc_detection/suggest.py --conf 0.4                 # --conf 0.5 si trop bruité
#    (par défaut : status=todo. Ajoute in_progress avec --status todo,in_progress)
```

**Deux façons d'obtenir les boîtes dans l'UI d'annotation :**

- **À la demande (recommandé)** : dans la fenêtre d'annotation, sélectionner un
  label par défaut puis cliquer **« 🔍 Détecter les blocs (IA) »**. Le serveur
  shelle vers le python de `bloc_detection` (`POST /api/image/<id>/detect`),
  détecte l'une courante (~10 s, cold start torch) et matérialise les boîtes.
- **En lot** : `suggest.py` pré-calcule pour toutes les unes `todo` ; le bouton
  **« ✨ Charger N suggestions »** matérialise les boîtes pré-calculées (instantané).

Dans les deux cas les boîtes deviennent des annotations normales → reclasser
(touches 1-9), ajuster, supprimer le bruit.

Le serveur d'annotation trouve l'interpréteur via la variable d'env
`BLOC_DETECTION_PYTHON` (défaut : `~/.conda/envs/bloc_detection/python.exe`) ;
seuil via `BLOC_DETECTION_CONF` (défaut 0.4).

### Post-traitement : suppression des boîtes qui se chevauchent

Le NMS d'Ultralytics filtre sur l'IoU, ce qui laisse passer deux gênes à
l'annotation : les quasi-doublons (IoU sous le seuil) et le *containment* (une
petite boîte presque entièrement dans une grande → IoU faible). `suggest.py`
ajoute un filtre par **recouvrement relatif à la plus petite boîte**
(`intersection / min(aire)`) : greedy par confiance décroissante, on garde la
plus confiante et on jette toute boîte recouverte au-delà du seuil.

- `--dedup 0.85` (défaut) : seuil de recouvrement. `--dedup 0` désactive.
- Plus bas (`0.7`) = plus agressif (supprime plus). Plus haut = ne supprime que
  les recouvrements quasi totaux.
- Côté bouton « Détecter », réglable sans toucher au code via la variable d'env
  `BLOC_DETECTION_DEDUP` (défaut 0.85).

## Structure

```
bloc_detection/
├── README.md
├── environment.yml
├── export_dataset.py   DB SQLite → dataset/ au format YOLO
├── train.py            fine-tuning Ultralytics (yolo11 | rtdetr)
├── infer.py            inférence + visualisation sur une une
├── dataset/            généré (gitignore) : images/ labels/ data.yaml
└── runs/               généré (gitignore) : poids + métriques par run
```
