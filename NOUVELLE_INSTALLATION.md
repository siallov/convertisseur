# Installer le convertisseur pour une autre chorale

Un service de conversion correspond à une instance Paheko. Pour deux associations indépendantes, prévoir deux services, deux dossiers privés et deux adresses HTTPS. Changer la destination du service existant ne crée pas une seconde installation et ne déplace pas les documents déjà déposés.

## Préparation

1. Installer les sources du convertisseur sur le PC ou serveur choisi avec Python et Audiveris. Audiveris est un programme distinct ; il n’est pas embarqué dans le module Paheko.
2. Importer `module_chorale_configurable.zip` dans Paheko. L’identifiant livré est `chorale` ; son nom et sa présentation sont configurables.
3. Créer un dossier de partitions dans Documents et un compte Paheko dédié au dépôt WebDAV, avec les droits nécessaires sur ce dossier.
4. Préparer l’adresse HTTPS et le tunnel ou reverse proxy du nouveau convertisseur, en conservant l’authentification du service. Ces opérations ne sont pas réalisées par le formulaire de paramètres.
5. Créer les comptes de conversion avec les outils fournis. Déclarer les administrateurs existants dans `private/administrators.json`, par exemple `["responsable"]`, droits 600. Les fichiers privés ne sont pas inclus dans la livraison.

## Paramètres

Lancer `python3 setup_paheko.py` dans le dossier du service pour une première configuration locale. Cet assistant demande les adresses et les identifiants, vérifie la connexion WebDAV et enregistre les paramètres privés. Le mot de passe se saisit sans être affiché.

Après démarrage du service, les mêmes paramètres sont disponibles dans **Tableau de bord administrateur → Paramètres de l’installation** :

| Paramètre | Exemple |
| --- | --- |
| Nom de la chorale | Ma chorale |
| Adresse HTTPS du convertisseur | `https://partitions.mon-association.fr` |
| Adresse WebDAV | `https://mon-association.paheko.cloud/dav/documents/` |
| Dossier dans Documents | `Partitions` ou `Musique/Partitions` |
| Chemin du module | `/m/chorale/` |
| Identifiant WebDAV | Compte Paheko dédié |
| Mot de passe WebDAV | Saisi dans le champ masqué |

Le formulaire enregistre la configuration ; il ne teste pas la connexion WebDAV. Effectuer une conversion de test, la vérifier puis confirmer son dépôt pour contrôler le chemin complet. L’assistant local effectue un contrôle préalable WebDAV.

Les paramètres sont conservés dans `private/paheko.json`, droits 600, avec les identifiants WebDAV. Le mot de passe n’est jamais renvoyé au navigateur. Laisser le champ vide conserve le mot de passe existant uniquement si la destination et l’identifiant ne changent pas. Une nouvelle destination nécessite de saisir son mot de passe.

Les changements s’appliquent sans redémarrage. Ils sont refusés tant qu’une conversion est en attente, en cours, à vérifier ou en cours de dépôt, pour éviter de l’envoyer à la mauvaise association. Les anciens fichiers restent à leur emplacement d’origine ; rien n’est migré.

## Réglages à reporter dans Paheko

Dans la configuration du module chorale : renseigner la nouvelle adresse du convertisseur et choisir le même dossier pour le lecteur (`documents/Partitions` pour un dossier WebDAV `Partitions`). Vérifier les droits des membres sur les documents. Les liens vers le lecteur, la fiche du chant et l’autorisation d’afficher la disponibilité sont générés à partir des paramètres du service.

Le chemin du module livré est `/m/chorale/`. Ne le changer que si le module a réellement été installé sous un autre identifiant.

## Membres et courriels

La synchronisation des membres, les accès API, l’expéditeur et les paramètres SMTP restent configurés séparément avec les outils existants. Pour une nouvelle installation, les configurer pour la nouvelle association, y compris l’adresse publique utilisée dans les courriels d’activation. Ne pas copier les fichiers privés d’une autre association.

## Vérification finale

Vérifier : connexion d’un membre, reconnaissance sans dépôt préalable, aperçu et écoute, validation du dépôt dans le bon dossier Paheko, ouverture dans le lecteur et accès du seul administrateur au tableau de bord. Le PC doit rester allumé pour servir les conversions.
