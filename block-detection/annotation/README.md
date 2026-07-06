# Outil d'annotation Oldspapers

Outil web pour annoter manuellement les blocs sur des unes de journaux historiques Gallica, en vue d'entraîner un modèle de Document Layout Analysis.

## Installation

```powershell
conda activate oldspapers
pip install flask
```

(les autres dépendances — PIL, sqlite3 — sont déjà dans l'env.)

## Workflow

### 1. Initialiser la base

```powershell
python annotation_tool/db.py
```

Crée `data/annotations.db` avec 10 labels par défaut : `title`, `subtitle`, `article`, `illustration`, `ad`, `separator`, `header`, `footer`, `table`, `caption`.

### 2. Télécharger des unes

```powershell
# Toutes les unes nationales d'une date
python annotation_tool/downloader.py --date 1930-05-25

# Une année entière, en limitant le total
python annotation_tool/downloader.py --year 1914 --target 200

# Plusieurs années en mélangeant l'ordre (anti rate-limit)
python annotation_tool/downloader.py --range 1900-1939 --shuffle --target 1000

# Quelques journaux seulement
python annotation_tool/downloader.py --year 1930 --journals le_figaro humanite
```

Options :
- `--delay N` : pause entre requêtes (1.5s défaut)
- `--target N` : arrêter après N unes téléchargées
- `--shuffle` : aléatoire (évite le ban si on tire des journaux différents)

Les images sont stockées dans `data/images/` et indexées dans la DB.

### 3. Annoter

```powershell
python annotation_tool/server.py
```

→ ouvre **http://localhost:5050**.

**Page d'accueil** : grille de toutes les unes téléchargées, filtres par journal/statut. Cliquer sur une vignette pour annoter.

**Page d'annotation** :
- **Touches 1-9** : choisir un label (correspond à l'ordre dans le panneau gauche)
- **Clic + drag** sur l'image : dessiner une bbox
- **Clic sur une bbox** : la sélectionner
- **Drag d'une bbox sélectionnée** : la déplacer
- **Drag des poignées blanches aux coins** : redimensionner
- **Suppr / Backspace** : effacer la bbox sélectionnée
- **Esc** : désélectionner / annuler le dessin en cours
- **Molette** : zoom (centré sur le curseur)
- **Clic droit + drag** : pan
- **F** : fit-to-view
- **Bouton "Sauver et marquer comme terminé"** : passe l'image en `done` et retour à la liste
- **Bouton "Skip"** : marque l'image comme `skipped`

Les annotations sont sauvées en temps réel à chaque action (création/déplacement/redimensionnement/suppression).

### 4. Exporter le dataset

Depuis l'interface : lien "Export JSON" en haut.
Ou directement : http://localhost:5050/api/export

Format compatible avec un futur entraînement ML :
```json
{
  "labels": {"title": {"id": 1, "color": "#e76f51"}, ...},
  "images": [
    {
      "id": 1,
      "slug": "le_figaro_1930-05-25",
      "journal": "Le Figaro",
      "date": "1930-05-25",
      "w": 5093, "h": 7012,
      "annotations": [
        {"id": 1, "label": "title",   "bbox": [810, 200, 4280, 850]},
        {"id": 2, "label": "article", "bbox": [105, 1500, 800, 6800]},
        ...
      ]
    }
  ]
}
```

## Structure

```
annotation_tool/
├── README.md
├── db.py              SQLite : schéma, helpers
├── downloader.py      Téléchargement de masse depuis Gallica
├── server.py          Serveur Flask
├── templates/
│   ├── index.html
│   └── annotate.html
├── static/
│   ├── style.css
│   └── annotate.js    Canvas drawing
└── data/
    ├── images/        JPG téléchargés (full résolution Gallica)
    ├── thumbs/        Vignettes ~400 px générées à la demande pour la grille
    └── annotations.db SQLite
```

## Tests

Suite pytest (38 cas) sur la couche DB et les routes Flask. Chaque test reçoit une DB SQLite vierge dans `tmp_path` — la base de prod `data/annotations.db` n'est jamais touchée.

```powershell
pip install pytest
pytest annotation_tool/tests -v
```

Couverture :
- `test_db.py` — schéma idempotent, dédup par `slug`, transitions de statut, CASCADE images → annotations, persistance entre connexions, règle métier `list_images_paginated` (les `done` / `skipped` ne reviennent jamais).
- `test_server.py` — toutes les routes, bascule auto `todo → in_progress` à l'ouverture, non-rétrogradation d'une image `done`, forme du JSON d'export, 404 sur image inconnue / fichier manquant. Le thread de réapprovisionnement (réseau Gallica) est neutralisé via monkeypatch.

## Labels par défaut

| Label | Description | Couleur |
|---|---|---|
| header | Bandeau / en-tête (titre journal, date, édition, infos pratiques) | rose |
| titre | Titre, sous-titre, titre d'article ou de section | terracotta |
| illustration | Photo, gravure, dessin, vignette | teal |
| bloc de texte | Corps d'article / paragraphes multiples formant un bloc | jaune |
| texte isolé | Slogan, accroche, ligne unique, encart bref | sandy orange |
| autres | Pub, tableau, filet, légende, pied de page, etc. | gris |

Pour ajouter/modifier les labels, éditer `DEFAULT_LABELS` dans `db.py` ou directement la table `labels`. Les renommages doivent passer par `LABEL_RENAMES` (migration douce qui préserve les annotations existantes).
