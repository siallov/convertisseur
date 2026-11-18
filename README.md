# Partition — PDF → MusicXML

Application locale en français. Import PDF par glisser-déposer, aperçu, file de conversion, journal et téléchargement `.musicxml` et `.mxl`. Un onglet « Extraire les paroles » récupère le texte d’une partition et produit le PDF sans paroles. Les documents restent sur votre ordinateur. Les polices Google sont optionnelles et remplacées automatiquement par une police système sans connexion.

La conversion n’utilise que la bibliothèque standard de Python. L’onglet « Paroles » a besoin de `requirements-paroles.txt` (PyMuPDF, numpy, SciPy, Pillow ; Verovio pour graver les MusicXML au format liseuse) ; sans ces bibliothèques, il l’indique et le reste fonctionne.

Les données sont rangées dans `data/` et `private/` à côté du programme quand ces dossiers existent (installations existantes), sinon dans le profil : `~/.local/share/atelier-partitions` (Linux) ou `%LOCALAPPDATA%\AtelierPartitions` (Windows). `PARTITIONS_HOME` choisit un autre dossier.

## Installer sur un autre PC (« Atelier partitions »)

```bash
python3 distribution/build.py
```

produit deux archives dans `dist/` :

- **Linux** (`…-linux.tar.gz`, Ubuntu 22.04+, Debian 12+ ou dérivé, 64 bits) : décompresser puis lancer `./installer.sh`. Il installe au besoin Python, Tesseract et Audiveris, crée l’environnement Python, la commande `atelier-partitions` et l’icône du menu.
- **Windows** (`…-windows.zip`, Windows 10 ou 11, 64 bits) : « Extraire tout », puis double-cliquer sur `installer.cmd`. Il installe au besoin Python (winget, pour l’utilisateur), Tesseract (winget) et Audiveris (MSI officiel), crée les raccourcis du Bureau et du menu Démarrer et l’entrée de désinstallation dans Paramètres > Applications. **Pas encore essayé sur un vrai PC Windows.**

Les deux installent aussi le français et l’anglais pour qu’Audiveris lise les paroles (dépôt officiel `tesseract-ocr/tessdata` 4.1.0). Chaque téléchargement a une version fixée et une empreinte SHA-256 vérifiée. Détails pour l’utilisateur dans `distribution/*/LISEZMOI.txt`. Le lanceur (`lanceur.py`) démarre l’application si besoin puis ouvre le navigateur ; `--arreter` l’arrête.

## Démarrage depuis les sources

1. Installez Python 3.10 ou plus récent.
2. Installez **Audiveris** depuis les versions officielles : https://github.com/Audiveris/audiveris/releases. Les installateurs récents incluent Java. Suivez les instructions de la version adaptée à votre système.
3. Si `audiveris` n’est pas dans le PATH, définissez sa commande :

   ```bash
   export AUDIVERIS_CMD='/chemin/vers/Audiveris/bin/Audiveris'
   # Ou, si votre distribution propose un JAR exécutable :
   # export AUDIVERIS_CMD='java -jar /chemin/vers/Audiveris.jar'
   ```

   Les paroles sont lues en français (`fra`). Pour une autre langue : `export AUDIVERIS_OCR_LANGUAGES='fra+eng'` (langues installées dans Audiveris).

4. Dans ce dossier, lancez :

   ```bash
   python3 app.py
   ```

5. Ouvrez http://127.0.0.1:8765. Ctrl+C arrête le serveur. `PORT=9000 python3 app.py` change le port.

Le badge indique si la commande du moteur est trouvée ; son bon fonctionnement est confirmé lors d’une conversion. Une seule reconnaissance s’exécute à la fois, avec quatre tâches maximum dans la file et 30 minutes maximum par tâche. Les résultats sont restaurés au prochain démarrage. Les tâches interrompues doivent être relancées.

## Formats et limites

Audiveris reconnaît les partitions imprimées ; un scan net et droit donne de meilleurs résultats. Les partitions manuscrites, annotations et mises en page complexes peuvent échouer. Vérifiez rythmes, voix, altérations et articulations dans MuseScore ou votre éditeur musical. Aucun score ni pourcentage de précision artificiel n’est généré.

Chaque mouvement exporté fournit un MXL compressé et un MusicXML décompressé. Le journal permet de comprendre les échecs. Ce serveur est prévu pour un usage personnel sur l’interface locale uniquement.

Documentation de la commande intégrée : https://audiveris.github.io/audiveris/_pages/guides/advanced/cli/

## Vérifications

```bash
python3 -m unittest discover -s tests -v
```

Les tests emploient un moteur simulé pour vérifier l’intégration et les erreurs. Une validation réelle de reconnaissance exige Audiveris et un PDF de partition : aucun des deux n’est fourni dans le dossier initial.

Les installations Linux dans `/opt/audiveris/bin/Audiveris` sont détectées automatiquement. Si `pdfinfo` (Poppler) est disponible, la résolution des PDF de grandes dimensions est réduite automatiquement pour rester sous la limite de pixels d’Audiveris. Le PDF original reste intact. Le moteur Java est lancé sans interface graphique.

## Format liseuse (PocketBook / Vivlio InkPad 3)

L’onglet « Format liseuse » (`/liseuse`) produit une copie de la partition au format de l’écran de l’InkPad 3 : pages de 118,9 × 158,5 mm (1404 × 1872 pixels à 300 ppp). Il accepte deux sortes de fichiers :

- **un PDF** : les marges blanches sont retirées et chaque système (portées et paroles ensemble) est replacé sur les pages, dans l’ordre, sans jamais être coupé entre deux pages. Les numéros de page et petits titres courants répétés sont retirés. Les PDF numériques restent vectoriels ; les scans gardent leur image d’origine. Les portées d’un même système sont reconnues au trait vertical (ou à l’accolade) qui les relie à gauche ; à défaut, à l’alignement des barres de mesure ;
- **un MusicXML** (`.musicxml`, `.xml`, ou `.mxl` compressé, par exemple exporté de MuseScore) : la partition est entièrement regravée à la taille de l’écran par [Verovio](https://www.verovio.org), en trois tailles de notes : grande (portée de 7,2 mm, comme sur papier), moyenne (par défaut) ou petite (plus de mesures par page). Le résultat reste vectoriel. Les notes des indications de tempo sont écrites en toutes lettres (« noire = 100 ») : les polices de texte n’ont pas le caractère ♩.

En ligne de commande : `python3 liseuse.py partition.pdf|partition.musicxml [sortie.pdf] [--taille grande|moyenne|petite]` (par défaut `partition_liseuse.pdf`, taille moyenne).

Besoins : PyMuPDF et numpy, dans `requirements-paroles.txt` ; SciPy pour reconnaître les systèmes des PDF (sans lui, la page est découpée sur les grands espaces blancs seulement, et un système peut alors être séparé de ses paroles) ; Verovio pour les MusicXML. Vérifiez l’aperçu avant de copier le fichier sur la liseuse.

Sur la liseuse, réglez le rafraîchissement complet de l’écran sur « Jamais » (ou un long intervalle) pour éviter le flash noir à chaque page.

## Importer un MusicXML déjà prêt

Dans « Convertir une partition », un fichier MusicXML (`.musicxml`, `.xml` ou `.mxl`, par exemple exporté de MuseScore) peut être déposé à la place d’un PDF. Il ne passe pas par Audiveris : il rejoint directement les conversions « à vérifier », avec son titre, son compositeur et son pupitre. On l’écoute, on nomme ses pupitres et on le dépose dans Paheko comme une reconnaissance terminée. Le dépôt contient alors le MusicXML et le MXL, sans PDF d’origine. L’import fonctionne même si Audiveris est absent.

## Partage avec la chorale

Mise en ligne sécurisée, dépôt automatique dans Paheko et comptes des choristes : voir `PARTAGE.md` et `paheko/INSTALLATION.md`.

## Vérification musicale et administration — 29 octobre 2026

Toute nouvelle conversion attend désormais à l’état « à vérifier ». Le bouton « Vérifier et écouter » affiche le MusicXML reconnu avec OpenSheetMusicDisplay 2.2.1 (bibliothèque et licence incluses localement), le PDF original et l’écoute synthétique par partie. Cette page ne corrige pas les notes et ne joue pas les reprises ni les nuances. Les fichiers sont privés jusqu’au clic « Valider et déposer dans Paheko ». L’utilisateur confirme sa vérification ; le dépôt existant sans remplacement est conservé. Les conversions déjà publiées restent consultables sans second dépôt. Les résultats à vérifier expirent aussi au bout de 24 heures.

Le tableau de bord `/admin` du convertisseur affiche les états des conversions, la file de traitement, le stockage temporaire sur une limite de 1 Gio et les 20 derniers échecs disponibles (rétention de 24 heures). Rafraîchissement toutes les 15 secondes. Accès limité aux comptes `andre` et `serge-p` par le fichier privé `administrators.json` ; refus par défaut si ce fichier est absent. Aucun secret n’est renvoyé dans les réponses du tableau de bord. Les autres comptes restent limités à leurs propres conversions.

Installation sur un autre PC : créer dans le dossier privé `administrators.json` contenant une liste des identifiants administrateurs existants, puis fixer ses droits à 600. Ce fichier spécifique à l’instance est exclu des archives.

Validation : 30 tests couvrant notamment l’absence de dépôt avant validation, le dépôt par le propriétaire, la protection contre les origines étrangères et les doubles clics, les droits du tableau de bord, l’isolation des comptes et la connexion. Les quatre tests de l’application ont été relancés après adaptation de l’état final attendu à « à vérifier » : ils passent.

## Pupitres et suivi de lecture

Dans « Vérifier et écouter », nommez les lignes reconnues (Alto, Ténor, Baryton, Basse ou nom personnalisé), puis enregistrez avant de valider le dépôt. Les noms sont intégrés aux fichiers MusicXML et MXL. Le lecteur propose une écoute seule, un volume par pupitre et un curseur synchronisé sur la partition reconnue. Une ligne qui mélange déjà plusieurs chanteurs nécessite une correction manuelle. Voir [PUPITRES_ET_LECTURE.md](PUPITRES_ET_LECTURE.md).
