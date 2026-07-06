# Convention d'annotation — détection de blocs (unes de presse)

But : des blocs **précis et complets** pour l'OCR (jamais un texte coupé), et des
**titres identifiables** pour pouvoir les apparier à leur article.

## Principe général

- **1 boîte = 1 zone de texte homogène et complète** — jamais un demi-paragraphe,
  jamais deux colonnes dans une même boîte.
- **Cadrage serré au texte** : on exclut les filets, cadres décoratifs et grands blancs.
- **Pas de chevauchement** entre deux boîtes (sinon l'OCR lit deux fois le même texte).
- **Toute la page** : chaque élément de texte est annoté (pas de trou).

## Les 5 classes

| classe | contenu | rôle |
|---|---|---|
| **header** | bandeau de tête : nom du journal, date, prix, édition | mobilier de page, 1× en haut |
| **titre** | tout **titre / intertitre** mis en avant (article OU encart OU sous-section) | appariable au bloc qui suit |
| **bloc de texte** | le **corps** d'un article (texte courant à lire en continu) | la matière première OCR |
| **illustration** | photo / dessin / gravure | sans sa légende |
| **texte isolé** | textes **périphériques** : légendes, encarts (météo, cours, avis), citations encadrées, **pub** | garde `bloc de texte` propre |

## Règles de découpage (dans l'ordre de priorité)

1. **Un titre est toujours une boîte `titre` à part**, séparée du corps — qu'il
   coiffe un article ou un encart. C'est ce qui permet l'appariement titre ↔ contenu.
2. **Test « titre ou pas »** :
   - texte **sur sa propre ligne**, mis en avant (gras / plus grand / centré) → **`titre`** ;
   - **label en ligne** au fil du texte (ex. « FRANCE. — … », « ITALIE. — … » en début
     de paragraphe) → **reste dans le `bloc de texte`** (ce n'est pas un titre).
3. **Corps d'article** : un article = **une boîte `bloc de texte` par colonne** qu'il
   occupe. On coupe uniquement à la **fin de colonne** (frontière de lecture naturelle),
   jamais en plein paragraphe.
4. **Encart autonome** (météo, cours, petit avis) = son **titre en `titre`** +
   le **reste en `texte isolé`**.
5. **Légende d'image** = `texte isolé`, séparée de l'`illustration`.
6. **Publicité** = `texte isolé` (son titre éventuel reste `titre`).

## Cas concrets (tirés de la validation)

- **Météo « LE TEMPS PROBABLE »** : « LE TEMPS PROBABLE » → `titre` ; les prévisions
  (Région parisienne, Manche…) → `texte isolé`.
- **Digest « HIER »** (Le Matin) : « HIER » est sur sa ligne, en gras → `titre` ;
  le texte dessous (FRANCE… ANGLETERRE… en ligne) → un seul `bloc de texte`.
- **Résultats d'élections** avec sous-sections « ALPES-MARITIMES », « BASSES-ALPES »… :
  chaque intitulé de département est **sur sa ligne** → `titre` ; la liste dessous → `bloc de texte`.

## Ce que ça corrige (vs le modèle v1)

Le modèle avait tendance à **isoler ces titres/intertitres** que la GT fondait dans
les blocs → comptés comme erreurs (dossiers `analyse/03_fp_granularite`,
`analyse/06_confusion_classe`). Avec cette convention, **c'est la GT qu'on met au niveau
du modèle** : on annote systématiquement ces titres à part. Résultat attendu : moins de
faux positifs « granularité » et une classe `titre` beaucoup plus nette.
