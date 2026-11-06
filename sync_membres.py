#!/usr/bin/env python3
"""Synchronise les comptes du convertisseur avec les membres actifs de Paheko.

  python3 sync_membres.py --configure   enregistre l'accès API (private/membres.json)
  python3 sync_membres.py --dry-run     montre ce qui serait fait
  python3 sync_membres.py               crée les comptes manquants, supprime ceux des anciens membres
  python3 sync_membres.py --configure-mail   enregistre le compte d'envoi des e-mails (private/smtp.json)
  python3 sync_membres.py --lien IDENTIFIANT nouveau lien d'activation (si le lien a expiré ou l'e-mail n'est pas arrivé)
  python3 sync_membres.py --nouveaux    affiche puis efface les liens qui n'ont pas pu être envoyés par e-mail

Seuls les comptes créés par cette synchronisation sont supprimés ; les autres (ex. administrateur) ne sont jamais touchés.
"""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import sys
import unicodedata
from email.message import EmailMessage
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import activation
import shared_server

STATE = shared_server.STATE
CONFIG = STATE / 'membres.json'
MANAGED = STATE / 'membres_geres.json'
PENDING = STATE / 'nouveaux_comptes.txt'
SMTP = STATE / 'smtp.json'


def write_private(path, text):
    STATE.mkdir(mode=0o700, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text)
    temporary.chmod(0o600)
    temporary.replace(path)


def load_config():
    config = json.loads(CONFIG.read_text())
    if not config['url'].startswith('https://') or not config['api_id'] or not config['api_secret'] or not config['categories']:
        raise ValueError('configuration invalide')
    return config


def fetch_members(config):
    """Returns [(paheko_id, display_name, email)] of members in the configured categories."""
    ids = ','.join(str(int(c)) for c in config['categories'])
    field = config.get('name_field', 'nom')
    if not re.fullmatch(r'[a-z0-9_]+', field):
        raise ValueError('nom de champ invalide')
    sql = f'SELECT id, {field} AS name, email FROM users WHERE id_category IN ({ids}) ORDER BY id'
    token = base64.b64encode(f"{config['api_id']}:{config['api_secret']}".encode()).decode()
    request = Request(config['url'].rstrip('/') + '/api/sql', data=urlencode({'sql': sql}).encode(), headers={'Authorization': 'Basic ' + token})
    with urlopen(request, timeout=60) as response:
        rows = json.loads(response.read())['results']
    return [(int(row['id']), str(row['name'] or ''), str(row.get('email') or '').strip()) for row in rows]


def username(member_id, name):
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_name.lower()).strip('-')[:30].strip('-') or 'choriste'
    return f'{slug}-{member_id}'


def plan(members, managed, accounts):
    """Pure decision: returns (to_create {id: username}, to_remove {id: username})."""
    current = {str(member_id): username(member_id, name) for member_id, name, _ in members}
    create = {i: u for i, u in current.items() if i not in managed and u not in accounts}
    remove = {i: u for i, u in managed.items() if i not in current}
    return create, remove


def smtp_config():
    try:
        return json.loads(SMTP.read_text())
    except (OSError, ValueError):
        return None


def activation_link(user):
    base = (smtp_config() or {}).get('public_url', 'https://partitions.audiveris.fr').rstrip('/')
    return f'{base}/activation?token={activation.create(user)}'


def send_mail(address, user, link):
    """True only if the message was handed to the SMTP server."""
    config = smtp_config()
    if not config or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', address):
        return False
    message = EmailMessage()
    message['Subject'] = 'Votre accès au convertisseur de partitions'
    message['From'] = config['from']
    message['To'] = address
    message.set_content(
        'Bonjour,\n\nUn compte vient de vous être créé pour le convertisseur de partitions de la chorale.\n'
        f'Votre identifiant : {user}\n\nChoisissez votre mot de passe avec ce lien personnel (valable 48 heures, à usage unique) :\n{link}\n\n'
        'Si vous n’attendiez pas ce message, ignorez-le.\n')
    try:
        with smtplib.SMTP_SSL(config['host'], int(config['port']), timeout=30) as server:
            server.login(config['user'], config['password'])
            server.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        return False


def sync(members, dry_run=False, allow_empty=False):
    managed = json.loads(MANAGED.read_text()) if MANAGED.exists() else {}
    create, remove = plan(members, managed, shared_server.accounts())
    if managed and not members and not allow_empty:
        raise SystemExit('Liste vide alors que des comptes existent : abandon par sécurité (--autoriser-liste-vide pour forcer).')
    if dry_run:
        return create, remove
    lines = []
    emails = {str(member_id): email for member_id, _, email in members}
    for member_id, user in create.items():
        # Nobody knows this password: the account is unusable until the member activates it.
        shared_server.store_account(user, secrets.token_urlsafe(24))
        managed[member_id] = user
        link = activation_link(user)
        if not send_mail(emails.get(member_id, ''), user, link):
            lines.append(f'{user}  {emails.get(member_id) or "(pas d’e-mail)"}  {link}\n')
    for member_id, user in remove.items():
        shared_server.remove_account(user)
        del managed[member_id]
    write_private(MANAGED, json.dumps(managed))
    if lines:
        old = PENDING.read_text() if PENDING.exists() else ''
        write_private(PENDING, old + ''.join(lines))
    return create, remove


def configure():
    default = 'https://chorale-bem.paheko.cloud'
    url = input(f'Adresse de Paheko [{default}] : ').strip().rstrip('/') or default
    api_id = input('Identifiant API (lecture seule) : ').strip()
    api_secret = getpass.getpass('Mot de passe API (non affiché) : ')
    categories = [int(c) for c in re.findall(r'\d+', input('Numéros des catégories de membres à synchroniser [1] : ') or '1')]
    name_field = input('Champ contenant le nom [nom] : ').strip() or 'nom'
    config = {'url': url, 'api_id': api_id, 'api_secret': api_secret, 'categories': categories, 'name_field': name_field}
    try:
        members = fetch_members(config)
    except (HTTPError, URLError, ValueError, KeyError) as error:
        sys.exit(f'Connexion ou requête refusée ({error}). Rien n’a été enregistré.')
    os.umask(0o077)
    write_private(CONFIG, json.dumps(config))
    print(f'Accès vérifié : {len(members)} membre(s) trouvé(s). Enregistré.')


def configure_mail():
    host = input('Serveur SMTP [mail.mailo.com] : ').strip() or 'mail.mailo.com'
    port = int(input('Port SSL [465] : ').strip() or 465)
    user = input('Adresse e-mail d’envoi : ').strip()
    password = getpass.getpass('Mot de passe de cette messagerie (non affiché) : ')
    public_url = input('Adresse publique du convertisseur [https://partitions.audiveris.fr] : ').strip().rstrip('/') or 'https://partitions.audiveris.fr'
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
    except (OSError, smtplib.SMTPException) as error:
        sys.exit(f'Connexion refusée ({error}). Rien n’a été enregistré.')
    os.umask(0o077)
    write_private(SMTP, json.dumps({'host': host, 'port': port, 'user': user, 'password': password, 'from': user, 'public_url': public_url}))
    print('Connexion à la messagerie vérifiée et enregistrée.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--configure', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--nouveaux', action='store_true')
    parser.add_argument('--configure-mail', action='store_true')
    parser.add_argument('--lien')
    parser.add_argument('--autoriser-liste-vide', action='store_true')
    args = parser.parse_args()
    if args.configure:
        return configure()
    if args.configure_mail:
        return configure_mail()
    if args.lien:
        if args.lien not in shared_server.accounts():
            raise SystemExit('Compte inconnu.')
        return print(activation_link(args.lien))
    if args.nouveaux:
        print(PENDING.read_text() if PENDING.exists() else 'Aucun nouveau compte.', end='')
        PENDING.unlink(missing_ok=True)
        return
    try:
        members = fetch_members(load_config())
    except (OSError, ValueError, KeyError, HTTPError, URLError) as error:
        raise SystemExit(f'Synchronisation impossible : {error}')
    create, remove = sync(members, args.dry_run, args.autoriser_liste_vide)
    prefix = 'À créer' if args.dry_run else 'Créés'
    print(f'{prefix} : {len(create)}  —  Supprimés : {len(remove)}' if not args.dry_run else f'À créer : {sorted(create.values())}  —  À supprimer : {sorted(remove.values())}')


if __name__ == '__main__':
    main()
