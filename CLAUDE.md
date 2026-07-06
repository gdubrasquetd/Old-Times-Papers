# Oldspapers — Contexte projet

## Objectif

Petite app web locale pour parcourir les unes des journaux français historiques numérisés par Gallica (BnF). L'utilisateur choisit une date entre 1850 et 1955 et voit s'afficher les unes des principaux quotidiens nationaux à cette date. Une timeline horizontale permet de naviguer entre les années en gardant le même jour/mois (ex. "le 25 mai dans la presse de 1880 à 1955").

## Architecture

**Un seul fichier** `gallica_unes_server.py` qui contient :
- Un serveur HTTP local (`http.server` stdlib) qui écoute sur `localhost:8765`
- La page HTML/CSS/JS embarquée dans une constante string `HTML_PAGE`
- Des endpoints qui proxifient l'API Gallica (pour contourner CORS)

**Pourquoi un serveur local ?** L'API SRU/Issues de Gallica n'envoie pas les en-têtes CORS, donc impossible d'appeler depuis un HTML ouvert en `file://`. Le serveur Python fait le proxy entre la page (qui parle à `localhost`) et Gallica.

## API Gallica utilisée

L'app utilise **l'API Issues officielle** documentée sur api.bnf.fr :
```
https://gallica.bnf.fr/services/Issues?ark=ark:/12148/{catalog_ark}/date&date={year}
```

Cette API renvoie en XML **tous les fascicules d'une année** pour un titre donné. C'est l'approche optimale :
- 1 requête / titre / année (au lieu de 1 / titre / jour)
- Pas de scraping → pas de ban anti-bot
- Cache mémoire par année très efficace

Les ARK de catalogue (`cbXXX`) des ~60 titres sont stockés dans le JS au début de `HTML_PAGE`, dans le tableau `NEWSPAPERS`.

## Backend HTTP

L'app utilise **`curl` en sous-processus** comme backend HTTP, pas `requests`. Raison : sous Windows avec concurrence threadée, `requests` avec Session partagée donne des `ConnectionReset` (WinError 10054). `curl` règle ça (chaque appel = nouvelle connexion TLS isolée, sa propre pile TLS).

Fallback sur `requests` si `curl` introuvable. Voir `_fetch_with_curl()` dans le code.

## Limites / problèmes connus

- **Rate-limit Gallica** : si tu fais > 30-40 requêtes en peu de temps, Gallica peut blacklister l'IP pour 15-60 min. Ça se manifeste par des `curl exit=35` (SSL connect error) en chaîne. Solution : attendre, et limiter la concurrence (`GALLICA_SEMAPHORE = threading.Semaphore(2)`).
- **Aucun cache disque** : tout en mémoire, perdu à l'arrêt. C'était un choix explicite (cf. discussion utilisateur) pour rester simple.
- **OCR variable** : Gallica a un OCR de qualité inégale sur la presse ancienne. Pas utilisé ici (on affiche juste les vignettes IIIF), mais à savoir si on veut faire de la recherche plein texte plus tard.

## Lancement

```powershell
conda activate oldspapers
python gallica_unes_server.py --verbose
```

Mode verbose = log de chaque résolution dans le terminal. Recommandé pour débugger.

## Fichiers

- `gallica_unes_server.py` — tout est dedans
- `environment.yml` — env conda (`name: oldspapers`, Python 3.12, requests en fallback)
- `requirements.txt` — alternative pip
- `test_connection.py` — diagnostic réseau / TLS / curl. À lancer si on a des erreurs systématiques.
- `test_api.py` — test minimal d'un appel à l'API Issues
- `README.md` — instructions d'installation

## Endpoints HTTP

- `GET /` → page HTML
- `GET /api/resolve?ark=cbXXX&date=YYYY-MM-DD` → JSON `{issue_ark: "bpt6kXXX"}` ou `{issue_ark: null, reason: "no_issue_on_date"}`. Utilise et alimente le cache mémoire (clé `(catalog_ark, year)`).
- `GET /api/cache` → stats du cache (`?clear=1` pour vider)
- `GET /debug?ark=cbXXX&date=YYYY-MM-DD` → page HTML de diagnostic

## Frontend

- Date picker HTML5 (`<input type="date">`)
- Boutons `← Jour` / `Jour →` pour navigation jour à jour
- Bouton `↻ Aujourd'hui` qui remet la date par défaut (= jour/mois courants en année cible, voir constante `DEFAULT_TARGET_YEAR` dans le JS)
- Timeline horizontale 1850-1955 sous le sélecteur :
  - Chaque année est une `.year-tick` cliquable
  - Décennies en gras avec fond teinté
  - Année active en rouge (couleur accent)
  - Sous chaque année : jour de la semaine pour la date courante
  - Point vert si l'année est déjà préchargée
  - Scroll horizontal automatique pour centrer l'année active
- Raccourcis clavier : `←`/`→` = jour, `Shift+←`/`Shift+→` = année (même jour), `Home` = défaut
- **Préchargement progressif** : 1,5s après chaque rendu, le JS lance des résolutions en arrière-plan pour Le Figaro sur les années adjacentes (1, -1, 2, -2, ...). Ça remplit le cache annuel côté serveur sans bloquer l'UI.

## Pistes d'évolution

- **Ajouter des titres régionaux** : la liste actuelle est très "presse parisienne". Pour Marseille / Lyon / Toulouse / Bordeaux il faudrait étendre le tableau `NEWSPAPERS` avec les ARK appropriés (chercher sur catalogue.bnf.fr).
- **Recherche plein texte** : Gallica a l'API `ContentSearch` qui cherche dans l'OCR d'un fascicule donné. Pourrait permettre "trouve-moi tous les fascicules qui mentionnent X autour de cette date".
- **Visu HD** : passer de `f1/full/400,/...` à `f1/full/800,/...` (ou utiliser le viewer IIIF Mirador embarqué pour un zoom progressif).
- **Téléchargement PDF** : Gallica permet le téléchargement direct du PDF du fascicule via `ark:/12148/{bptXXX}.pdf`. Bouton à ajouter sur chaque carte.
- **Export d'une page statique** pour une date donnée (par exemple pour partager).
- **Mode comparatif** : afficher la une du même journal à la même date sur N années en grille (utiliser la timeline pour sélectionner).

## Style et conventions

- Code Python en français pour les commentaires fonctionnels, anglais pour les noms de fonctions/variables
- HTML/CSS embarqués dans le Python comme string brute (raw string `r"""..."""`) — pas idéal pour l'édition mais permet de garder un déploiement mono-fichier
- Pas de dépendance externe nécessaire en runtime (curl est livré avec Windows 10+)

## Historique de la discussion utilisateur

L'utilisateur (basé en France, francophone) a commencé par demander une présentation de Gallica, puis voulait afficher les unes des journaux à une date historique précise (25 mai 1936 = Front populaire). On est passé par plusieurs itérations :
1. HTML statique → cassé par CORS
2. Serveur Python local → résout CORS mais échoue sur scraping (rate-limit)
3. **Solution actuelle** : API Issues officielle + curl + cache mémoire + timeline horizontale

L'utilisateur préfère la simplicité — il a demandé explicitement de retirer le cache disque qui était overkill. Il privilégie aussi une vraie API documentée plutôt que du scraping.

## État au 25 mai 2026 (fin de session)

**Bloqué par un rate-limit Gallica** suite aux nombreux essais de scraping de la veille. Les requêtes HEAD passent (test_api.py = OK) mais les GET avec corps XML échouent en `exit=35` (Recv failure). Décision : laisser reposer le quota côté Gallica, reprendre demain avec une IP fraîche.

### À faire demain en priorité

1. **Lancer `python test_api.py`** d'abord pour vérifier que Gallica répond correctement (GET avec corps, pas juste HEAD)
2. **Démarrer le serveur** : `python gallica_unes_server.py --verbose`
3. **Identifier les ARKs fautifs** : en mode verbose, on a vu hier que plusieurs titres retournent `0 numéros` sur l'API Issues. Pour chacun, ouvrir dans le navigateur :
   ```
   http://localhost:8765/debug-raw?ark=<l'ark>&year=1930
   ```
   Si le XML retourné contient `0 fascicules trouvés`, l'ARK est probablement faux. Chercher le bon ARK sur https://gallica.bnf.fr (rechercher le titre + presse, regarder l'URL du calendrier).
4. **Mettre à jour la liste `NEWSPAPERS`** dans `gallica_unes_server.py` (chercher le commentaire `// ====== GRANDS QUOTIDIENS NATIONAUX`).

### ARKs déjà corrigés cette session (source officielle BnF Europeana Newspapers)

- L'Écho de Paris : `cb343558471` → `cb34429768r`
- Le Gaulois : `cb343555036` → `cb32779904b`
- L'Intransigeant : `cb32789550n` → `cb32793876w`

### ARKs encore à vérifier (suspects "0 numéros" hier soir)

- Excelsior : `cb327711069`
- L'Action française : `cb34431915c`
- Le Journal : `cb327800782`
- L'Œuvre : `cb32830704w`
- La Croix : `cb343631418`
- L'Humanité : `cb327877302` (a marché à un moment, à reconfirmer)
- Journal des débats : `cb39294634r` (a marché à un moment)
- Le Figaro : `cb34355551z` (a marché à un moment)

### Si rate-limit persiste

- Tester avec un VPN comme option B
- L'endpoint `/debug-raw` permet de tester un ARK à la fois sans déclencher 17 requêtes
- En cas de fenêtre courte sans blocage, exporter les ARKs résolus puis travailler en mode "tout en cache"
