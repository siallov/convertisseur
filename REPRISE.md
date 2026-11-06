# Reprise à zéro — convertisseur de partitions de la chorale

Ce document explique comment tout reconstruire si le PC est perdu ou réinstallé, et comment dépanner. Il ne contient aucun secret : les mots de passe, jetons et clés vivent dans `private/` (jamais dans Git) et doivent être sauvegardés à part.

## 1. Vue d’ensemble

```
Choriste ──HTTPS──> Cloudflare ──tunnel──> ce PC : shared_server.py (127.0.0.1:8766)
                                                │  Audiveris (reconnaissance OMR)
                                                ├─> WebDAV ──> Paheko Documents/Partitions ──> Lecteur de partitions
                                                └─< API Paheko (membres) ── sync_membres.py ── e-mail d’activation
```

| Élément | Valeur |
|---|---|
| Dossier du projet | `/home/andre/Documents/ChatGPT/pdf to MXL` |
| Adresse publique | https://partitions.audiveris.fr |
| Paheko | https://chorale-bem.paheko.cloud |
| Tunnel Cloudflare | `partitions-chorale-pc` (route vers `http://127.0.0.1:8766`, HTTP et non HTTPS) |
| Service local | `shared_server.py`, port 8766 |
| Moteur OMR | `/opt/audiveris/bin/Audiveris` |
| Données temporaires | `private/jobs` (effacées après 24 h) |

Le PC doit rester allumé, connecté et sans mise en veille.

## 2. Sauvegarder (à faire régulièrement)

Hors Git, copiez `private/` sur un support sûr : il contient

- `accounts.json` : comptes et empreintes de mots de passe ;
- `tunnel.token` : jeton Cloudflare ;
- `paheko.json` : accès WebDAV de dépôt ;
- `membres.json` : clé API Paheko (lecture) ;
- `smtp.json` : compte d’envoi des e-mails ;
- `membres_geres.json`, `activations.json` : état de la synchronisation.

Sans cette sauvegarde, il faudra recréer chaque secret (étapes 5 à 9). Le code est dans Git (`git log`).

## 3. Installer le logiciel

1. Linux avec Python 3 (`python3 --version`), Java requis par Audiveris.
2. Audiveris installé dans `/opt/audiveris` (voir https://github.com/Audiveris/audiveris/releases). Test : `/opt/audiveris/bin/Audiveris -help`.
3. Optionnel : `poppler-utils` (`pdfinfo`) pour réduire les PDF très grands ; Node.js (`node --check static/app.js`) pour contrôler le script du site.
4. Récupérer le projet (copie du dossier ou dépôt Git), puis vérifier :

```bash
cd "/home/andre/Documents/ChatGPT/pdf to MXL"
python3 -m unittest discover -s tests     # 20 tests, environ 30 s
```

5. `cloudflared` : binaire dans `.tools/cloudflared` (https://github.com/cloudflare/cloudflared/releases, version linux amd64).

## 4. Premier compte administrateur

```bash
python3 shared_server.py --add-user andre
```

Mot de passe de 12 caractères minimum, saisi dans le terminal. Une page d’installation locale existe aussi : `setup_shared.py` (port 8767).

## 5. Cloudflare : adresse HTTPS permanente

1. Compte Cloudflare, domaine `audiveris.fr` (noms de serveurs Cloudflare).
2. Zero Trust → Réseaux → Tunnels → créer `partitions-chorale-pc` (type cloudflared). Copier le **jeton** dans `private/tunnel.token` (`chmod 600`).
3. Dans le tunnel → application publiée : sous-domaine `partitions`, domaine `audiveris.fr`, service **`http://127.0.0.1:8766`** (avec `https://` on obtient une erreur 502).
4. Test : `curl -I https://partitions.audiveris.fr` doit répondre **401** (compte exigé).

## 6. Démarrage automatique (systemd utilisateur)

Créer dans `~/.config/systemd/user/` :

`partitions-shared.service`
```ini
[Unit]
Description=Convertisseur de partitions (service partage, 127.0.0.1:8766)
After=network-online.target

[Service]
WorkingDirectory=/home/andre/Documents/ChatGPT/pdf to MXL
ExecStart=/usr/bin/python3 -u shared_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

`partitions-tunnel.service`
```ini
[Unit]
Description=Tunnel Cloudflare des partitions
After=network-online.target partitions-shared.service
Wants=network-online.target

[Service]
WorkingDirectory=/home/andre/Documents/ChatGPT/pdf to MXL
ExecStart=/bin/sh -c 'exec ./.tools/cloudflared tunnel --no-autoupdate run --token-file private/tunnel.token'
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

`partitions-sync.service` et `partitions-sync.timer` (étape 9)
```ini
[Unit]
Description=Synchronisation des comptes choristes depuis Paheko

[Service]
Type=oneshot
WorkingDirectory=/home/andre/Documents/ChatGPT/pdf to MXL
ExecStart=/usr/bin/python3 sync_membres.py
```
```ini
[Unit]
Description=Synchronisation des comptes choristes toutes les 10 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
```

Activation :

```bash
systemctl --user daemon-reload
systemctl --user enable --now partitions-shared partitions-tunnel
loginctl enable-linger "$USER"        # démarrage au boot sans session ouverte
```

## 7. Paheko : compte de dépôt (WebDAV)

1. Configuration → Catégories de membres → créer `convertisseur` : droit Documents « lecture & écriture », rien d’autre, sans accès à la configuration.
2. Créer un membre de cette catégorie (identifiant = son e-mail, mot de passe fort).
3. Sur le PC : `python3 setup_paheko.py` (adresse `https://chorale-bem.paheko.cloud/dav/documents/`, dossier `Partitions`). Le script teste la connexion avant d’enregistrer.
4. Relancer : `systemctl --user restart partitions-shared`.

Les fichiers sont déposés sous le nom du PDF et ne sont jamais écrasés. Ce compte ne peut pas supprimer : un administrateur supprime les fichiers erronés dans Documents.

## 8. Paheko : lecteur et droits

1. Catégorie « Membres actifs » : droit **Documents = Lecture** (sinon les choristes ne voient rien). Conséquence : ils voient tous les documents de Documents.
2. Module `lecteur_partitions` (Configuration → Extensions → Modifier le code, `index.html`) : le lecteur lit `documents/Partitions` et construit les liens `/documents/Partitions/<nom>`. Deux lignes à vérifier si le lecteur est réinstallé : `parent="documents/Partitions"` et `'/documents/Partitions/'`.
3. Le lecteur lit `.pdf`, `.mid`, `.musicxml` et `.xml`, mais **pas `.mxl`** (compressé) : le convertisseur dépose donc les deux formats, seul `.musicxml` s’affiche.
4. Module `partitions_omr` (lien vers le convertisseur) : importer `paheko/partitions_omr.zip` (Configuration → Extensions → Importer), ou modifier sa page directement (`paheko/modules/partitions_omr/index.html`). Détails : `paheko/INSTALLATION.md`.

## 9. Comptes automatiques des choristes

1. Paheko → Configuration → Fonctions avancées → API : créer un identifiant (ex. `sync`) en **lecture seule**. Attention : tout accès API permet de télécharger la base complète (mots de passe chiffrés inclus).
2. `python3 sync_membres.py --configure` (adresse de Paheko, identifiant et mot de passe API, numéros des catégories à synchroniser — « Membres actifs » est le n° 1 —, champ du nom : `nom`).
3. `python3 sync_membres.py --configure-mail` : SMTP `mail.mailo.com`, port 465 (SSL), adresse d’envoi, mot de passe de la messagerie, adresse publique `https://partitions.audiveris.fr`.
4. Simulation : `python3 sync_membres.py --dry-run`, puis `systemctl --user enable --now partitions-sync.timer`.

Fonctionnement : chaque membre des catégories reçoit un compte `prenom-nom-numero` et un e-mail contenant un lien personnel (48 h, usage unique, page `/activation`) pour choisir son mot de passe. Les membres sortis des catégories perdent leur compte. Les comptes créés à la main (ex. `andre`) ne sont jamais touchés.

## 10. Vérifier que tout marche

| Test | Résultat attendu |
|---|---|
| `curl -I https://partitions.audiveris.fr` | 401 |
| Se connecter sur le site, envoyer un PDF | conversion, message « Envoyé dans Paheko Documents » |
| Paheko → Documents/Partitions | fichiers nommés d’après le PDF |
| Lecteur de partitions (compte choriste) | la partition apparaît |
| Nouveau membre « Membres actifs » | e-mail reçu en moins de 10 min |

## 11. Dépannage

| Symptôme | Cause et remède |
|---|---|
| 502 sur l’adresse publique | La route du tunnel doit être `http://127.0.0.1:8766` ; vérifier `systemctl --user status partitions-shared partitions-tunnel`. |
| « Origine refusée » sur un formulaire | Les navigateurs envoient `Origin: null` avec une politique de référent `no-referrer` ; elle est réglée sur `same-origin` dans `shared_server.py` et `setup_shared.py`. L’adresse publique doit être celle de `smtp.json` (`public_url`). |
| Conversion « Envoi Paheko impossible » | Vérifier `private/paheko.json` (relancer `setup_paheko.py`) ; un fichier déjà présent est signalé et non écrasé. |
| Les choristes ne voient pas les partitions | Droit Documents « Lecture » manquant pour « Membres actifs ». |
| Nouveau membre sans e-mail | `python3 sync_membres.py --nouveaux` affiche les liens non envoyés ; `--lien IDENTIFIANT` crée un nouveau lien. |
| Lien d’activation expiré | `python3 sync_membres.py --lien IDENTIFIANT` |
| Trop de tentatives (429) | Attendre 5 minutes (limite de 10 échecs par adresse). |
| Résolution DNS locale périmée | Tester avec `curl --resolve partitions.audiveris.fr:443:188.114.97.2 …` |
| Après modification du code | `systemctl --user restart partitions-shared` |

Journaux : `journalctl --user -u partitions-shared -u partitions-tunnel -u partitions-sync -n 50`. Le service ne journalise volontairement ni adresses, ni noms de fichiers, ni identités.

## 12. Limites connues

- Une seule conversion à la fois (file de 4), PDF limités à 80 Mo, stockage temporaire de 1 Go.
- Résultats temporaires effacés après 24 h (la copie dans Paheko est conservée).
- La reconnaissance musicale n’est pas parfaite : toujours vérifier dans un éditeur de partitions.
- Cloudflare voit le trafic HTTPS ; les PDF restent stockés sur ce PC.
- Les fichiers de test déposés par erreur dans Paheko ne peuvent être supprimés que par un administrateur.

## 13. Fichiers du projet

| Fichier | Rôle |
|---|---|
| `app.py` | serveur local, conversion Audiveris, nommage des résultats, dépôt Paheko |
| `shared_server.py` | service partagé authentifié, comptes, page `/activation` |
| `paheko_upload.py`, `setup_paheko.py` | dépôt WebDAV et sa configuration |
| `sync_membres.py`, `activation.py` | synchronisation des membres, liens d’activation |
| `setup_shared.py` | page d’installation locale (compte, jeton) |
| `static/` | pages web |
| `paheko/` | module Paheko et notice d’installation |
| `tests/` | 20 tests automatiques |
| `PARTAGE.md`, `README.md` | documentation détaillée |
