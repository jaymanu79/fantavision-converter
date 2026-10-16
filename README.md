# fantavision-converter

Convertisseur des animations **Fantavision** (Broderbund, Amiga, 1988) vers des **SVG animés** lisibles dans n'importe quel navigateur (Firefox, Chrome, Safari, Edge).

Fantavision est un logiciel d'animation vectorielle à images clés : le fichier ne contient que des polygones aux images clés, et le lecteur calcule les images intermédiaires par interpolation (« tweening »). Le SVG produit fait exactement la même chose grâce aux animations SMIL (`<animate attributeName="points">`), sans JavaScript. Les fichiers restent minuscules (1 à 60 Ko) et nets à toutes les tailles.

## Utilisation

Python 3, aucune dépendance.

Pour convertir tous les films du dossier `movies/` d'un coup :

```bash
./convert-movies.sh              # écrit les SVG dans svg-output/
./convert-movies.sh --step 0.04  # les options sont transmises à fant2svg.py
```

Ouvrez ensuite les fichiers de `svg-output/` dans un navigateur (la visionneuse d'images d'Ubuntu, par exemple, ne joue pas les animations SVG). Les SVG ne sont pas versionnés : ils se régénèrent à partir des sources.

Pour un seul film :

```bash
python3 fant2svg.py FILM [sortie.svg] [--step 0.05] [--arrival] [--debug]
```

| Option | Effet |
|---|---|
| `--step S` | durée de base d'une étape intermédiaire en secondes (défaut 0,05 s), à laquelle s'ajoute le champ vitesse de l'en-tête / 60 s |
| `--arrival` | le nombre d'intermédiaires d'une image clé compte depuis la précédente (par défaut : vers la suivante) |
| `--debug` | affiche en pointillés roses les objets de type `$7000`, normalement masqués |

Les films se trouvent dans le dossier `Movies` de la disquette Fantavision. Pour les extraire d'une image `.adf`, n'importe quel outil de lecture de disquettes Amiga (OFS) convient.

## Ce qui est géré

- Polygones pleins, lignes (polylignes) et contours ; morphing même quand le nombre de points change d'une image clé à l'autre (rééchantillonnage).
- Couleurs interpolées par index de palette, étape par étape, et palette propre à chaque image clé.
- Bitmaps ILBM (décor de fond et objets), convertis en PNG intégrés, avec transparence sur la couleur 0.
- Textes (`CSTR`), avec une police de substitution du navigateur.
- Objets qui apparaissent ou disparaissent d'une image clé à l'autre.
- Boucle : si la dernière image clé contient des objets, elle se retransforme en la première.
- Rapport d'aspect des pixels Amiga (NTSC 320×200 affiché en 4:3).

Non géré : les sons 8SVX (le SVG ne joue pas de son) et les polices bitmap Amiga.

## Le format FANT

Fichier IFF standard. Tous les nombres sont des mots 16 bits big-endian (signés pour les coordonnées).

```
FORM FANT
  FHDR (92 o)       en-tête global
  FORM ILBM ...     décor (si présent) puis bitmaps
  FORM 8SVX + SEFX  sons et leurs déclenchements
  FRAM (88 o)       début d'une image clé
    POLY ...        objets de l'image clé, dans l'ordre d'affichage (arrière -> avant)
    CSTR ...        texte puis nom de police, pour chaque objet texte
  FRAM ...
```

### FHDR

| Offset | Contenu |
|---|---|
| 0 | `$0040` |
| 2 | `$0010` |
| 4 | profondeur (5 = 32 couleurs) |
| 6, 8 | largeur, hauteur (320×200, 352×276…) |
| 10 | vitesse (0, 2, 15 dans les exemples ; interprétée ici comme un délai en 1/60 s) |
| 12 | palette : 32 mots `$0RGB` |
| 76 | nombre d'images clés |
| 78 | nombre de sons |
| 80 | nombre de bitmaps (hors décor) |
| 82 | présence d'un décor (1 = le premier ILBM est le fond) |

### FRAM

| Offset | Contenu |
|---|---|
| 0–7 | inconnu (presque toujours 0) |
| 8 | nombre d'images intermédiaires |
| 10, 12 | sons déclenchés sur deux voies (-1 = aucun, -2 = arrêt ?) |
| 14 | nombre de `POLY` qui suivent |
| 16 | palette de l'image clé : 32 mots `$0RGB` |

### POLY

En-tête de 18 mots, puis *n* couples (x, y).

| Mot | Contenu |
|---|---|
| 0 | nombre de points *n* |
| 1 | type et drapeaux : `0` polygone plein, `1` ligne, `2` contour, `3` texte, `$x007` bitmap (`$1007`, `$2007`), `$7000` objet invisible (cadre de groupe ?) |
| 2 | index de couleur |
| 3–6 | boîte englobante x1, y1, x2, y2 |
| 7 | identifiant (non utilisé pour l'appariement) |
| 8 | toujours 256 |
| 9 | inconnu (0 à 7) |
| 10–12 | bitmap : index, largeur, hauteur (le point unique est le centre) |
| 13–15 | texte : longueur de la chaîne, ?, taille de police << 8 |

Deux images clés successives sont appariées objet par objet, selon leur position dans la liste.

## Points encore incertains

- Sens exact du champ vitesse et du compte d'intermédiaires (vers la suivante ou depuis la précédente).
- Signification des drapeaux `$1000`, `$2000`, `$4000` et du mot 9 des `POLY`.
- Rôle des objets `$7000`.
- Premiers mots des `FRAM`.

La meilleure façon de trancher serait de comparer avec FantaPlayer sous émulateur, ou de désassembler `fantaplayer`. Les contributions sont bienvenues.

## Films (`movies/`)

Le dossier `movies/` contient les fichiers `FORM FANT` d'origine des films de démonstration de la disquette Fantavision Amiga : *Anthropology*, *Bird*, *Dinosaurs*, *Eyes*, *Jaws* et *PA*. La copie utilisée est la version PAL diffusée en 1988 par le groupe ANC Europe. *PA* (352×276, format PAL) n'est peut-être pas un film Broderbund. Les icônes de deux autres films, *Clock* et *CarCrash!*, sont présentes sur la disquette, mais pas les films eux-mêmes ; un film `anc`, lancé au démarrage par le script de FantaPlayer, manque aussi.

Fantavision et ses films de démonstration sont © Broderbund.
