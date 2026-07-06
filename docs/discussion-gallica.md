# OCR maison sur les unes Gallica

## Contexte

Gallica protège l'accès à son OCR via un challenge CAPTCHA (ALTCHA) —
inaccessible programmatiquement. L'idée ici : faire notre propre OCR
à partir des images IIIF qu'on récupère déjà.

---

## Ce qu'on a

- Les images IIIF des unes, jusqu'en résolution 800px :
  `https://gallica.bnf.fr/iiif/ark:/12148/{bpt6k}/f1/full/800,/0/native.jpg`
- On peut monter jusqu'à 2000px+ sans ban (les images IIIF ne sont
  pas protégées par ALTCHA, contrairement au texteBrut)
- Un serveur local Python déjà en place

---

## Options OCR

### 1. Tesseract (local, gratuit)
- Le plus classique, tourne en local, pas de dépendance cloud
- Support du français (`fra`) intégré
- Qualité correcte sur texte imprimé moderne, **variable sur presse ancienne**
- Nécessite une image propre (binarisation préalable)
- Intégration Python : `pytesseract` (wrapper) ou `tesseract` en subprocess

```
pip install pytesseract pillow
# + installer Tesseract binaire : https://github.com/UB-Mannheim/tesseract/wiki
```

**Verdict pour la presse 1850-1955** : résultats inégaux. Le Figaro des
années 1920-1930 (imprimé propre) = bon. Presse du XIXe siècle
(typographie ancienne, jauni) = difficile.

---

### 2. EasyOCR (local, deep learning)
- Basé sur des modèles PyTorch, meilleur sur typographies variées
- Support français natif
- Plus lourd (GPU recommandé, mais tourne en CPU)
- Meilleur que Tesseract sur les polices gothiques/archaïques

```
pip install easyocr
```

---

### 3. Claude Vision (API Anthropic)
- Envoyer l'image IIIF directement à Claude claude-opus-4-7 ou claude-sonnet-4-6
- Demander : "Transcris le texte visible sur cette une de journal"
- Qualité **excellente** sur typographies difficiles, comprend le contexte
- **Coût** : ~$0.003-0.01 par image selon résolution et modèle
- Nécessite une clé API Anthropic

---

### 4. Google Vision / Azure OCR (cloud)
- Très bonne qualité, support historique correct
- Payant après quota gratuit
- Dépendance externe

---

## Questions à trancher

1. **Qualité vs coût** : Tesseract gratuit mais imparfait, ou Claude API payant mais excellent ?
2. **Quand OCR-iser ?** À la demande (clic utilisateur) ou batch en arrière-plan ?
3. **Stocker quoi ?** Texte brut ? JSON avec blocs/coordonnées ? Les deux ?
4. **Résolution image** : 800px suffisant ? Ou monter à 1500-2000px pour meilleur OCR ?
5. **Preprocessing** : faut-il binariser/débruiter avant OCR (PIL/OpenCV) ?

---

## Architecture envisagée

```
[clic utilisateur sur "Lire l'OCR"]
        ↓
Vérifier cache disque (cache/ocr/{bpt6k}.txt)
        ↓ si absent
Télécharger image IIIF haute résolution (ex: 1500px)
        ↓
[preprocessing optionnel : grayscale, threshold]
        ↓
OCR (Tesseract / EasyOCR / Claude Vision)
        ↓
Stocker en cache disque
        ↓
Afficher dans la page article
```

Nouveau endpoint serveur : `GET /api/ocr-local?ark={bpt6k}`
(distinct de l'ancien `/api/ocr` qui tentait texteBrut)
