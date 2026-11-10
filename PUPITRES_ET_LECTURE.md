# Pupitres et suivi de lecture

## Première étape

Après une conversion PDF, ouvrir **Vérifier et écouter**. Le panneau **Nommer et séparer les pupitres** présente les lignes reconnues, leur portée et leur nombre de notes. Donner un nom à chacune, par exemple Alto, Ténor, Baryton, Basse ou Accompagnement. Un nom personnalisé de 80 caractères maximum est également possible.

Cliquer sur **Enregistrer les pupitres dans les fichiers**. Le service crée une partie MusicXML distincte pour chaque couple portée/ligne rythmique reconnu et inscrit les noms dans le MusicXML et son MXL. Les notes, durées, silences, accords et liaisons conservés doivent être vérifiés par l’écoute avant de confirmer le dépôt. Le PDF original reste disponible pour comparaison. Les modifications se font avant publication ; une partition déjà déposée n’est pas modifiée automatiquement.

Chaque pupitre dispose d’une sélection, d’un bouton **seul** et d’un réglage de volume. Le lecteur privilégie le MusicXML lorsqu’un fichier MIDI du même nom existe, afin d’utiliser les noms et la séparation validés.

Le curseur suit la partition reconnue dans l’aperçu et dans le lecteur Paheko. Il tient compte du tempo, de la vitesse choisie, des pauses et des changements de position. La case **Suivre la partition pendant l’écoute** permet de masquer le curseur et d’arrêter le défilement automatique. Le PDF original ne reçoit pas de curseur : son image n’a pas les positions musicales nécessaires.

## Limites explicites

- Le système ne déduit pas un pupitre de la hauteur des notes. Si Audiveris a mélangé plusieurs chanteurs dans une seule ligne ou dans des accords, ils restent ensemble. Un accord ambigu entre deux lignes entraîne un refus de séparation plutôt qu’une suppression de notes.
- Les noms doivent être vérifiés : une ligne peut être une voix chantée, un accompagnement ou une erreur de reconnaissance.
- Le son est synthétique. Reprises, nuances et ornements ne sont pas interprétés comme dans un enregistrement de la chorale.
- L’original reconnu est conservé dans les résultats privés pour permettre de renommer à nouveau sans accumuler les transformations. Il suit la même expiration que les résultats de conversion.
- La nouvelle fonction ne modifie pas les anciens fichiers présents dans Documents. Les nouvelles conversions publiées bénéficient des noms validés. Les MusicXML existants sont lus en séparant leurs lignes encodées ; les noms génériques sont présentés comme « Pupitre 1 », etc.

## Implémentation et vérification

`convertisseur/pupitres.py` identifie les couples partie/portée/ligne et prépare les exports. Les routes `/api/jobs/<id>/pupitres` vérifient le propriétaire, l’origine des écritures et l’état de vérification avant de permettre un changement. La modification est interdite pendant ou après un dépôt réussi. La liste des noms est conservée dans les résultats privés et dans les métadonnées de publication.

Le moteur `score-engine.js` est identique dans le convertisseur et le module Paheko. Il conserve les numéros de portée et de ligne pour l’écoute, aligne la mesure suivante sur la ligne la plus longue et conserve les silences terminaux. La lecture s’arrête correctement en fin de morceau. Le volume nul d’un pupitre n’émet aucune note.

Le rendu utilise la copie locale d’OpenSheetMusicDisplay déjà fournie avec le convertisseur ; dans Paheko elle est chargée à la demande d’une partition MusicXML, sans ressource externe. Licence et lien vers les sources sont fournis dans `modules/chorale/assets/`. Cette bibliothèque augmente la taille du module ; une mesure complète en réseau lent reste à effectuer.

Vérifications : 46 tests du convertisseur et 18 tests de l’installateur réussis (64 au total), suites Python du convertisseur et de l’installateur, tests Node du tempo et du curseur, cinq assertions XML avec le DOMParser du navigateur et essais d’interface sur une partition fictive à deux lignes, renommées Alto et Baryton. Les essais contrôlent l’export MXL/XML, la conservation des silences, la pause, la recherche dans le temps et la fin de lecture. Aucun fichier réel de partition n’a été transformé pour ces essais.

Références techniques : [durées MusicXML](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/duration/), [curseur OpenSheetMusicDisplay](https://opensheetmusicdisplay.github.io/classdoc/classes/Cursor.html), [chronologie de l’itérateur](https://opensheetmusicdisplay.github.io/classdoc/classes/MusicPartManagerIterator.html).

Déploiement vérifié sur `chorale-bem.paheko.cloud` : partition existante affichée, six lignes reconnues sélectionnables, volume nul essayé sans erreur, curseur avancé pendant la lecture puis figé en pause. Le service local répond après redémarrage. Les noms de cette ancienne partition n’ont pas été réécrits. Le dossier d’assets du module évite le chemin `/vendor/`, réservé par le pare-feu Paheko.


## Regrouper les lignes reconnues en pupitres

Dans « Nommer et regrouper les pupitres », donnez le même nom aux lignes à réunir. Les espaces extérieurs et la casse sont ignorés pour le regroupement. Une portée est créée par nom ; les voix simultanées restent distinctes dans le MusicXML pour préserver rythmes, accords et paroles. L’écoute propose un contrôle par pupitre regroupé.

Le bouton « Proposer le regroupement des lignes bis / ter » enlève uniquement les suffixes bis et ter dans les champs. Vérifiez les affectations puis cliquez sur « Enregistrer les pupitres dans les fichiers ». La proposition ne modifie aucun fichier avant cet enregistrement. Aucun regroupement par hauteur n’est effectué.

Le regroupement est possible entre portées et entre parties d’origine ; les notes sont réunies dans une seule portée (une voix par ligne). Les exports XML et MXL sont mis à jour ensemble. Pour annuler un regroupement, attribuez à nouveau des noms distincts : la transformation repart toujours du MusicXML original conservé.


## Notes et silences communs ; accords mal affectés

Ouvrez « Corriger les notes et silences par mesure » dans le convertisseur. La liste des mesures suit les parties et la numérotation du MusicXML original. Pour chaque note reconnue, cochez un pupitre pour l’y affecter, ou plusieurs pour une note commune. Les silences peuvent être partagés de la même façon. Dans un accord, chaque tête de note possède une ligne distincte. « Affectation d’origine » annule la correction de cette seule note.

Cliquez sur « Enregistrer les pupitres dans les fichiers », puis vérifiez le rendu et l’écoute avant le dépôt. Les corrections sont conservées lors d’un renommage et appliquées au XML et au MXL. L’original est conservé. Les choix restent limités aux pupitres de la même portée d’origine. Il n’y a aucune duplication ou répartition automatique selon la hauteur. Cette fonction ne corrige pas les hauteurs ou les durées mal reconnues par Audiveris.
