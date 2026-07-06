# Old Times Papers — Navigateur d'unes de la presse française historique

Petit serveur Python local pour parcourir les unes des journaux français
numérisés par Gallica (BnF) sur une date donnée, entre ~1850 et 1955.

## Installation (une seule fois)

Depuis un PowerShell **Anaconda Prompt** dans le dossier du projet :

```powershell
conda env create -f environment.yml
```

Cela crée un environnement conda nommé **`oldspapers`** avec Python 3.12 et `requests`.

Si l'environnement existe déjà et que tu veux le mettre à jour après modification
de `environment.yml` :

```powershell
conda env update -f environment.yml --prune
```

## Utilisation

À chaque session :

```powershell
conda activate oldspapers
python gallica_unes_server.py
```

Le navigateur s'ouvre automatiquement sur `http://localhost:8765`.

Pour arrêter le serveur : `Ctrl+C` dans le terminal.

### Options

```powershell
python gallica_unes_server.py --verbose   # log chaque résolution dans le terminal
```

### Endpoint de debug

Pour tester la résolution d'un titre à une date donnée sans passer par l'interface :

```
http://localhost:8765/debug?ark=cb34355551z&date=1936-05-25
```

## Désinstallation

Pour supprimer complètement l'environnement :

```powershell
conda env remove -n oldspapers
```

## Alternative sans conda

Si tu préfères un venv Python classique :

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python gallica_unes_server.py
```

## Structure du projet

```
Oldspapers/
├── environment.yml          # définition de l'env conda
├── requirements.txt         # alternative pip
├── gallica_unes_server.py   # serveur + page HTML embarquée
└── README.md                # ce fichier
```

## Comment ça marche

1. La page HTML (servie à `/`) affiche un sélecteur de date et une grille de cartes.
2. Pour chaque titre actif à la date choisie, le JS appelle `/api/resolve?ark=...&date=...`.
3. Le serveur Python suit la redirection de Gallica :
   `https://gallica.bnf.fr/ark:/12148/{cb_catalogue}/date{YYYYMMDD}`
   → retourne l'ARK du fascicule (`bpt6k...`).
4. Le JS charge la vignette via l'API IIIF de Gallica :
   `https://gallica.bnf.fr/iiif/ark:/12148/{bpt6k...}/f1/full/400,/0/native.jpg`

Le serveur sert uniquement de proxy pour contourner CORS (Gallica n'autorise pas
les requêtes JS cross-origin depuis un navigateur).

## Sources

- [Gallica BnF](https://gallica.bnf.fr) — bibliothèque numérique (contenu domaine public)
- [API BnF](https://api.bnf.fr) — documentation des API Gallica/IIIF/SRU
