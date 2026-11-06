# Partager le convertisseur depuis ce PC

## État

L’application locale (`app.py`, port 8765) conserve son historique existant. Le service partagé (`shared_server.py`, port 8766) utilise un stockage distinct `private/jobs`. Tous ses accès nécessitent un compte. Chaque compte ne voit que ses propres conversions ; un compte partagé entre plusieurs personnes donne accès aux mêmes résultats.

Les mots de passe sont stockés sous forme d’empreintes PBKDF2-SHA256 salées (600 000 itérations). Le navigateur demande l’identifiant et le mot de passe avec sa fenêtre de connexion HTTP. La publication doit impérativement passer par HTTPS. Fermez votre session de navigateur après utilisation sur un ordinateur partagé.

Les copies temporaires sont effacées après 24 heures (le fichier déposé dans Paheko est conservé), avec vérification toutes les minutes. Une seule conversion fonctionne à la fois. Les transferts sont limités à 80 Mo et la file à quatre conversions. Le stockage refuse de nouveaux imports au-delà de 1 Go (les conversions déjà en cours peuvent dépasser ce seuil).

## Créer les comptes et démarrer

Dans le dossier de l’application :

```bash
python3 shared_server.py --add-user andre
python3 shared_server.py --add-user choriste1
python3 shared_server.py
```

Les mots de passe sont demandés dans le terminal et ne s’affichent pas. Cette méthode manuelle sert surtout aux administrateurs : les comptes des choristes sont créés automatiquement (voir plus bas). Pour révoquer un compte, arrêtez le service, supprimez son entrée du fichier `private/accounts.json`, puis redémarrez. Les comptes sont relus à chaque requête.

Le service écoute uniquement sur http://127.0.0.1:8766. Aucun accès extérieur n’est activé par ces commandes.

## Adresse HTTPS permanente

Prérequis : un compte Cloudflare et un domaine dont vous pouvez gérer le DNS. Le sous-domaine fourni par Paheko.cloud ne permet pas de gérer son DNS.

1. Créez votre compte sur https://dash.cloudflare.com/sign-up et ajoutez votre domaine. Les éventuels achat de domaine et acceptation des conditions sont à effectuer par vous-même.
2. Suivez la procédure officielle de tunnel nommé : https://developers.cloudflare.com/tunnel/get-started/
3. Publiez par exemple `partitions.votre-domaine.fr`, avec le service d’origine `http://127.0.0.1:8766`.
4. Installez et démarrez le connecteur sur ce PC avec le jeton fourni par Cloudflare. Ce jeton est secret et ne doit pas être inclus dans le module Paheko, un ZIP, une capture ou un dépôt Git.
5. Vérifiez depuis une connexion extérieure qu’un visiteur non connecté ne peut ni voir les partitions ni télécharger les résultats, puis testez avec deux comptes distincts.
6. Remplacez le lien local du module Paheko par l’URL HTTPS vérifiée.

Le trafic HTTPS passe par Cloudflare : cet intermédiaire traite les requêtes et les fichiers transmis. Les PDF restent stockés dans le dossier privé sur ce PC. N’exposez pas le port local 8765 et n’activez pas la mise en cache des réponses de conversion.

## Mise en service actuelle

Services utilisateur systemd (`~/.config/systemd/user/`, démarrage au boot grâce à `loginctl enable-linger`) :

- `partitions-shared.service` : le service partagé (à redémarrer après une modification du code : `systemctl --user restart partitions-shared`) ;
- `partitions-tunnel.service` : le tunnel Cloudflare (jeton dans `private/tunnel.token`) ;
- `partitions-sync.timer` : synchronisation des comptes avec Paheko toutes les 10 minutes.

Le PC doit rester allumé, connecté et sans mise en veille pendant les conversions.

Les tunnels temporaires `trycloudflare.com` sont destinés aux essais ; ils ne fournissent pas l’adresse permanente souhaitée.

## Intégration Paheko

### Nom des fichiers et dépôt automatique

Les téléchargements portent le nom du PDF d’origine (`Mon chant.pdf` donne `Mon chant.musicxml`, puis `-2`, `-3` pour les mouvements supplémentaires). Après chaque conversion réussie, les fichiers sont déposés dans Documents/Partitions de Paheko par WebDAV (`paheko_upload.py`). Un fichier déjà présent n’est jamais écrasé.

Configuration (une fois) : `python3 setup_paheko.py`, avec un compte Paheko dédié disposant du droit Documents « lecture & écriture » ; les identifiants sont enregistrés dans `private/paheko.json`. Sans ce fichier, rien n’est envoyé. Ce compte ne peut pas supprimer : un administrateur doit supprimer les fichiers déposés par erreur.

Le « Lecteur de partitions » lit le dossier Documents/Partitions. La catégorie « Membres actifs » a donc le droit de lecture sur Documents : les choristes voient tous les documents.

### Comptes des choristes automatiques

`sync_membres.py` lit par l’API Paheko (identifiant en lecture seule) les membres des catégories choisies et crée un compte par membre (`prenom-nom-numero`). Le membre reçoit un e-mail avec un lien personnel (48 h, usage unique, page `/activation`) pour choisir son mot de passe : aucun mot de passe n’est envoyé. Les comptes des membres sortis des catégories sont supprimés ; les comptes créés à la main ne sont jamais touchés. Une liste vide renvoyée par Paheko est refusée par sécurité.

Mise en place :

```bash
python3 sync_membres.py --configure        # accès API Paheko (private/membres.json)
python3 sync_membres.py --configure-mail   # compte d’envoi SMTP (private/smtp.json)
python3 sync_membres.py --dry-run          # simulation
systemctl --user enable --now partitions-sync.timer
```

Autres commandes : `--lien IDENTIFIANT` (nouveau lien si le précédent a expiré), `--nouveaux` (liens non envoyés par e-mail, par exemple membre sans adresse).

Attention : tout identifiant API Paheko, même en lecture seule, permet de télécharger la base complète (mots de passe chiffrés des membres inclus). Gardez `private/` en mode 700 et ne le partagez jamais.
