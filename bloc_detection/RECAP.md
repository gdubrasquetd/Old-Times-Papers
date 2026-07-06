# Récapitulatif — Détection de blocs (DLA) sur les unes de presse

Ce document résume tout le travail réalisé : les modèles entraînés, l'outil
d'annotation et son évolution, la boucle de correction, et l'état final.

Voir aussi : [`README.md`](README.md) (workflow de base) et
[`CONVENTION_ANNOTATION.md`](CONVENTION_ANNOTATION.md) (règles d'annotation).

---

## 1. Objectif

Segmenter chaque une de presse historique en blocs typés (titre, corps
d'article, illustration…) pour, ensuite, faire de l'OCR **par bloc** et pouvoir
apparier titres et articles. GPU cible : RTX 3050 Laptop (4 Go VRAM) → modèles
légers (`yolo11s`), batch 2, `imgsz` 1280, AMP.

## 2. Deux modèles distincts

### a) Détecteur mono-classe `bloc` — aide à l'annotation
- Dataset : `dataset_blocs/` (toutes les boîtes fusionnées en une classe `bloc`).
- Sert le bouton **« 🔍 Détecter »** de l'outil (via `suggest.py`).
- Poids en service : `runs/blocs_yolo11s/weights/best.pt` (backups `best_v1_backup.pt`, `best_v2_backup.pt`).

| version | unes (train/val) | boîtes | P | R | mAP50 | mAP50-95 |
|---|---|---|---|---|---|---|
| blocs_yolo11s_v2 | 18 / 5 | 1198 | 0.805 | 0.811 | 0.857 | 0.636 |
| **blocs_yolo11s_v3** (déployé) | 39 / 11 | 3297 | 0.867 | 0.876 | 0.911 | 0.712 |

### b) Détecteur multi-classes (5 classes) — analyse de mise en page
- Dataset : `dataset/` exporté avec `--exclude autres` (classe `autres` retirée : trop peu d'exemples).
- Classes : `header`, `titre`, `illustration`, `bloc de texte`, `texte isolé`.
- **Pas de bascule en « production »** : chaque version vit dans son run.
- Source des propositions de correction (`review_proposals.py` → `multiclass_yolo11s_v3`).

Global (classe `all`) au fil des tours de correction :

| version | boîtes | mAP50 | mAP50-95 | rappel |
|---|---|---|---|---|
| multiclass_yolo11s_v1 | 3289 | 0.807 | 0.648 | 0.756 |
| multiclass_yolo11s_v2 | 3457 | 0.858 | 0.690 | 0.816 |
| **multiclass_yolo11s_v3** | 3500 | 0.867 | 0.703 | 0.856 |

Détail par classe (v3, validation finale) :

| classe | mAP50 | mAP50-95 | rappel |
|---|---|---|---|
| header | 0.995 | 0.835 | 0.976 |
| bloc de texte | 0.967 | 0.889 | 0.931 |
| titre | 0.909 | 0.645 | 0.857 |
| illustration | 0.859 | 0.749 | 0.917 |
| texte isolé | 0.605 | 0.399 | 0.598 |

**Conclusion** : `header` et `bloc de texte` (les plus utiles pour l'OCR) sont
solides ; `titre` bon ; `texte isolé` reste la classe dure (hétérogène :
légendes, encarts, avis) mais son rappel est passé de 0.48 (v2) à 0.60 (v3).
Niveau jugé **suffisant pour l'étape de détection de blocs**.

> Caveat : les splits val diffèrent d'une version à l'autre (la GT change avec
> les corrections), donc ce ne sont pas des A/B parfaits — mais la tendance sur
> 3 tours est nette et cohérente.

## 3. La convention d'annotation

Formalisée dans [`CONVENTION_ANNOTATION.md`](CONVENTION_ANNOTATION.md). Idées clés :
- 1 boîte = 1 zone de texte homogène et **complète** (jamais un fragment coupé), cadrée serré, sans chevauchement.
- **Un titre est toujours une boîte `titre` à part** (article, encart ou sous-section) → appariable au contenu.
- Test « titre ou pas » : sur sa propre ligne et mis en avant → `titre` ; label en ligne au fil du texte → reste dans le bloc.
- Corps d'article = une boîte `bloc de texte` **par colonne**, coupée seulement en fin de colonne.
- `texte isolé` = textes périphériques (légendes, encarts, pub) pour garder `bloc de texte` propre.

C'est l'harmonisation de la GT selon cette convention qui a fait progresser les modèles v1 → v3.

## 4. L'outil d'annotation (`../annotation_tool/`)

Beaucoup de travail a porté sur l'outil lui-même. Principales évolutions :

### Performance
- **Refactor canvas → viewport** : les scans font jusqu'à 68 Mpx. L'image est
  désormais un `<img>` transformé en CSS (composité par le navigateur) et le
  `<canvas>` overlay fait la taille du viewport (ne dessine que les boîtes).
  Fin des lags au zoom/pan/dessin.
- **Rendu throttlé** (`requestAnimationFrame`) : un repaint par frame max.
- **Création de boîte optimiste** : la boîte apparaît au lâcher du clic, la
  persistance se fait en arrière-plan (plus de latence).
- **Mode verbose** (touche `v`) : mesure paint / frames zoom-pan / longtasks,
  renvoyé au serveur (`/api/clientlog` → `data/perf.log`). Détection de lag
  toujours active (blocages > 150 ms remontés même sans verbose).

### Robustesse
- **`watchdog.py`** : surveille le serveur (`/api/health`) et le **redémarre
  automatiquement** s'il meurt ou zombifie (process vivant mais muet).
  Incidents horodatés dans `data/watchdog.log`. **Lancer via `python watchdog.py`.**
- Correctifs d'encodage (console Windows cp1252 → utf-8) pour `server.py` et `watchdog.py`.

### Aides à l'annotation
- **Détection IA** (bouton) : `suggest.py` propose le découpage, avec
  post-traitement **anti-doublons** (`--dedup`, recouvrement relatif à la plus petite boîte).
- **Fusion des blocs chevauchants** (bouton bascule) : tient compte de
  l'orientation (colonne verticale vs titre horizontal) et de l'alignement.
- **Surlignage des chevauchements** (touche `h`).
- **Tout supprimer** (avec undo groupé).

### Mode révision / correction (la boucle vertueuse)
- **`review_proposals.py`** : compare les prédictions multi-classes à la GT et
  émet des propositions typées :
  - `split_title` : séparer un titre détecté dans un bloc / ajouter un titre manquant ;
  - `reclassify` : changer la classe d'une boîte.
  - Stockées dans la table `proposals` (rien n'est modifié sans validation).
- **Révision par une** : bouton « 🔧 Réviser » → modale avant/après,
  Accepter / Corriger / Refuser (tout annulable Ctrl+Z). « Corriger » applique
  puis zoome sur la zone.
- **Onglet Corrections global** (`/corrections`) : bouton « Calculer » lance le
  modèle sur **toutes les unes `done`** d'un coup (modèle chargé une seule fois),
  affiche toutes les propositions groupées par une. Page **2 colonnes** :
  éditeur (iframe) à gauche, propositions à droite → on corrige sans quitter l'onglet.
  Vignettes avant/après servies par `/api/image/<id>/crop` (pas de chargement des images 68 Mpx).

### Téléchargement équilibré
- `downloader.download_batch` priorise les **journaux sous-représentés** dans les
  annotés (`db.count_by_journal(status='done')`, round-robin) pour équilibrer le jeu.

## 5. La boucle vertueuse (workflow d'un tour)

```powershell
conda activate bloc_detection

# 1. (dans l'outil) onglet /corrections -> « Calculer » -> traiter les propositions
#    (Accepter / Corriger / Refuser). Le modèle source = review_proposals.py DEFAULT_WEIGHTS.

# 2. réexporter la GT corrigée
python bloc_detection/export_dataset.py --exclude autres        # multi-classes
python bloc_detection/export_dataset.py --single-class          # aide annotation

# 3. réentraîner
python bloc_detection/train.py --data bloc_detection/dataset/data.yaml \
       --model yolo11s --imgsz 1280 --batch 2 --epochs 200 --patience 50 \
       --name multiclass_yolo11s_vN

# 4. comparer la mAP par classe vN vs vN-1, puis rebrancher review_proposals.py
#    (DEFAULT_WEIGHTS -> vN) pour que les propositions du tour suivant soient meilleures.
```

Chaque tour : GT plus cohérente → modèle meilleur → propositions meilleures → révision plus rapide.

## 6. Fichiers clés

| fichier | rôle |
|---|---|
| `export_dataset.py` | DB → dataset YOLO. Options `--single-class`, `--exclude` |
| `train.py` | fine-tuning yolo11 / rtdetr |
| `infer.py` | inférence + visualisation sur une une |
| `suggest.py` | détecteur mono-classe → suggestions (bouton « Détecter »), `--dedup` |
| `review_proposals.py` | propositions de correction (mode révision), `--all` pour le batch |
| `CONVENTION_ANNOTATION.md` | règles d'annotation |
| `runs/<name>/analyse/` | analyses de prédictions (crops par type d'erreur), généré à la demande |
| `../annotation_tool/watchdog.py` | supervision + auto-restart du serveur |

## 7. Piste restante

Le seul palier non franchi : la **précision de `texte isolé`** (classe
intrinsèquement hétérogène). Options si besoin plus tard : annoter plus de cas
ciblés, la scinder en sous-types (`légende` / `encart`), ou l'accepter telle
quelle (elle remplit déjà son rôle : écarter ces textes du flux d'articles).

---

## 8. État au 2 juillet 2026

- Détection de blocs **validée** comme suffisante pour la suite (OCR par bloc).
- Modèle de référence : **`multiclass_yolo11s_v3`** (5 classes).
- Aide à l'annotation en service : **`blocs_yolo11s_v3`** (mono-classe).
- Prochaine grande étape : intégration **détection → OCR par bloc** dans Oldspapers.
