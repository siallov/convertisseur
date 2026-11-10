# Partition — PDF → MusicXML

Application locale en français, sans dépendance Python externe. Import PDF par glisser-déposer, aperçu, file de conversion, journal et téléchargement `.musicxml` et `.mxl`. Les documents restent sur votre ordinateur, dans `data/` (supprimez ce dossier lorsque l’application est arrêtée pour effacer l’historique). Les polices Google sont optionnelles et remplacées automatiquement par une police système sans connexion.

## Démarrage

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

## Partage avec la chorale

Mise en ligne sécurisée, dépôt automatique dans Paheko et comptes des choristes : voir `PARTAGE.md` et `paheko/INSTALLATION.md`.

## Vérification musicale et administration — 29 octobre 2026

Toute nouvelle conversion attend désormais à l’état « à vérifier ». Le bouton « Vérifier et écouter » affiche le MusicXML reconnu avec OpenSheetMusicDisplay 2.2.1 (bibliothèque et licence incluses localement), le PDF original et l’écoute synthétique par partie. Cette page ne corrige pas les notes et ne joue pas les reprises ni les nuances. Les fichiers sont privés jusqu’au clic « Valider et déposer dans Paheko ». L’utilisateur confirme sa vérification ; le dépôt existant sans remplacement est conservé. Les conversions déjà publiées restent consultables sans second dépôt. Les résultats à vérifier expirent aussi au bout de 24 heures.

Le tableau de bord `/admin` du convertisseur affiche les états des conversions, la file de traitement, le stockage temporaire sur une limite de 1 Gio et les 20 derniers échecs disponibles (rétention de 24 heures). Rafraîchissement toutes les 15 secondes. Accès limité aux comptes `andre` et `serge-p` par le fichier privé `administrators.json` ; refus par défaut si ce fichier est absent. Aucun secret n’est renvoyé dans les réponses du tableau de bord. Les autres comptes restent limités à leurs propres conversions.

Installation sur un autre PC : créer dans le dossier privé `administrators.json` contenant une liste des identifiants administrateurs existants, puis fixer ses droits à 600. Ce fichier spécifique à l’instance est exclu des archives.

Validation : 30 tests couvrant notamment l’absence de dépôt avant validation, le dépôt par le propriétaire, la protection contre les origines étrangères et les doubles clics, les droits du tableau de bord, l’isolation des comptes et la connexion. Les quatre tests de l’application ont été relancés après adaptation de l’état final attendu à « à vérifier » : ils passent.

## Pupitres et suivi de lecture

Dans « Vérifier et écouter », nommez les lignes reconnues (Alto, Ténor, Baryton, Basse ou nom personnalisé), puis enregistrez avant de valider le dépôt. Les noms sont intégrés aux fichiers MusicXML et MXL. Le lecteur propose une écoute seule, un volume par pupitre et un curseur synchronisé sur la partition reconnue. Une ligne qui mélange déjà plusieurs chanteurs nécessite une correction manuelle. Voir [PUPITRES_ET_LECTURE.md](PUPITRES_ET_LECTURE.md).
