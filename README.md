# Tanuki Tools

Application Windows pour traduire les scripts CSV et manipuler les archives d'images `TArc1.10` de jeux utilisant le moteur TanukiSoft.

Le projet est actuellement en phase alpha et a été développé puis testé avec **Shoujo Ramune**. Les archives originales ne sont jamais écrasées.

## Fonctionnalités

### Dialogues CSV

- détecte les scripts déclarés dans `_project.csv` ;
- exporte uniquement la colonne `%text%` vers des TXT lisibles ;
- affiche le fichier, le numéro de ligne, le personnage et la voix pour chaque bloc ;
- réimporte les traductions sans modifier les autres colonnes ;
- vérifie que les CSV sources n'ont pas changé depuis l'export ;
- crée les CSV traduits dans un nouveau dossier.

### Archives d'images

- lit les archives TanukiSoft `TArc1.00` et `TArc1.10` ;
- extrait les PNG/JPG en conservant les dossiers `bg`, `ev`, `cc`, `tn`, etc. ;
- détecte les fichiers réellement modifiés grâce à un manifeste SHA-256 ;
- contrôle les dimensions des images de remplacement ;
- reconstruit un nouveau `.tac` en conservant les entrées non modifiées ;
- rouvre l'archive créée et vérifie chaque remplacement octet par octet.

## Utilisation de l'exécutable

1. Télécharger `TanukiTools.exe` depuis la page Releases du dépôt.
2. Le placer près des fichiers du jeu ou le lancer depuis n'importe quel dossier.
3. Sélectionner le dossier CSV ou l'archive `.tac` dans l'interface.

### Traduire les dialogues

1. Choisir le dossier `datascn.tac` contenant les CSV.
2. Cliquer sur **Analyser**.
3. Garder les scripts du projet sélectionnés et cliquer sur **Exporter les TXT**.
4. Modifier uniquement le contenu situé après les marqueurs `<<<...:TRADUCTION>>>`.
5. Choisir un dossier de sortie vide et cliquer sur **Créer les CSV traduits**.

Le fichier `_tanuki_tools_manifest.json` associe chaque bloc TXT à sa cellule CSV. Les anciens exports contenant `_t07_manifest.json` restent acceptés.

### Extraire et remplacer des images

1. Choisir `datapic.tac` ou `datapicl.tac`.
2. Extraire les images.
3. Modifier les fichiers sans changer leurs chemins ni leurs dimensions.
4. Choisir le dossier extrait comme dossier de remplacements.
5. Créer une nouvelle archive, par exemple `datapic_fr.tac`.

Le manifeste `.tanuki_tools_images.json` permet de ne réinjecter que les images modifiées. L'archive source reste intacte.

## Encodage français

Les CSV d'origine utilisent généralement **CP932 (Shift-JIS)**. Cet encodage ne représente pas les caractères `é`, `à`, `ç`, `œ`, etc.

Le mode recommandé **Compatible jeu** simplifie donc les accents (`é` → `e`, `œ` → `oe`) tout en préservant les caractères japonais. Le mode UTF-8 avec BOM conserve les accents, mais doit être considéré comme expérimental tant que le jeu ciblé ne l'a pas validé.

## Installation depuis les sources

Prérequis : Windows et Python 3.11 ou plus récent.

```powershell
git clone <URL-DU-DEPOT>
cd Tanuki-Tools
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m tanuki_tools
```

Le code utilise :

- Tkinter pour l'interface graphique ;
- Pillow pour lire et vérifier les images ;
- PyCryptodome pour Blowfish ;
- la bibliothèque standard Python pour CSV, zlib et la reconstruction des archives.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Les tests GitHub Actions fonctionnent sans fichiers du jeu. Pour activer localement le test supplémentaire de lecture réelle :

```powershell
$env:TANUKI_TEST_DATAPIC = "C:\chemin\vers\datapic.tac"
python -m unittest discover -s tests -v
```

## Construire l'exécutable

```powershell
.\build.ps1
```

Le résultat est créé dans `dist\TanukiTools.exe`. Le dossier `dist` et les fichiers de jeux sont exclus de Git.

## Structure du dépôt

```text
tanuki_tools/       paquet Python et ressource tanuki.lst
tests/              tests unitaires et d'intégration locale
.github/workflows/  tests automatiques GitHub Actions
build.ps1           création de l'exécutable Windows
main.py             point d'entrée PyInstaller
```

## Crédits et formats

Le décodage TArc et la liste de noms s'appuient sur les recherches du projet [GARbro](https://github.com/morkt/GARbro). Sa licence MIT est conservée dans `licenses/GARbro_LICENSE.txt` et les autres mentions se trouvent dans `THIRD_PARTY_NOTICES.md`.

N'ajoutez pas d'archives, d'exécutables ou d'images provenant d'un jeu au dépôt public.
