"""Envoi d'un PDF au format liseuse sur une PocketBook, par Send-to-PocketBook.

La liseuse a une adresse personnelle en @pbsync.com : tout PDF reçu à cette adresse,
d'un expéditeur autorisé, est téléchargé par la liseuse dès qu'elle a le Wi-Fi.
L'e-mail part du compte d'envoi de la chorale (private/smtp.json, le même que pour les
liens d'activation). Chaque choriste doit donc autoriser l'adresse de la chorale une fois.

Seules les adresses @pbsync.com sont acceptées : le convertisseur ne peut pas servir à
envoyer des fichiers ailleurs.
"""
import json
import re
import smtplib
import threading
import time
from email.message import EmailMessage

import chemins

SMTP = chemins.PRIVATE / 'smtp.json'
ADDRESS = re.compile(r'[A-Za-z0-9._%+-]{1,64}@pbsync\.com')
MAX_BYTES = 20 * 1024 * 1024        # largement assez pour une partition, sous les limites des messageries
PER_HOUR = 30                       # envois par personne et par heure
_SENT: dict[str, list[float]] = {}
_LOCK = threading.Lock()


def config():
    try:
        value = json.loads(SMTP.read_text())
    except (OSError, ValueError):
        return None
    needed = ('host', 'port', 'user', 'password', 'from')
    return value if isinstance(value, dict) and all(value.get(key) for key in needed) else None


def sender():
    """L'adresse d'envoi à autoriser sur la liseuse, ou '' si l'envoi n'est pas configuré."""
    return (config() or {}).get('from', '')


def valid_address(address):
    return bool(ADDRESS.fullmatch(address or ''))


def _allowed(who):
    now = time.time()
    with _LOCK:
        recent = [t for t in _SENT.get(who, []) if now - t < 3600]
        if len(recent) >= PER_HOUR:
            _SENT[who] = recent
            return False
        _SENT[who] = recent + [now]
        return True


def send(address, pdf, filename, who='local'):
    """Envoie le PDF. Lève ValueError (demande refusée) ou RuntimeError (envoi impossible)."""
    settings = config()
    if settings is None:
        raise RuntimeError('L’envoi par e-mail n’est pas configuré sur ce convertisseur.')
    address = (address or '').strip()
    if not valid_address(address):
        raise ValueError('Indiquez l’adresse Send-to-PocketBook de la liseuse (elle se termine par @pbsync.com).')
    if not pdf.startswith(b'%PDF-') or len(pdf) > MAX_BYTES:
        raise ValueError('Ce fichier n’est pas un PDF de moins de 20 Mo.')
    if not filename.lower().endswith('.pdf'):
        filename += '.pdf'
    if not _allowed(who):
        raise ValueError('Trop d’envois en une heure. Réessayez plus tard.')
    message = EmailMessage()
    message['Subject'] = filename[:-4]
    message['From'] = settings['from']
    message['To'] = address
    message.set_content('Partition envoyée depuis le convertisseur de la chorale (format liseuse).\n')
    message.add_attachment(pdf, maintype='application', subtype='pdf', filename=filename)
    try:
        with smtplib.SMTP_SSL(settings['host'], int(settings['port']), timeout=60) as server:
            server.login(settings['user'], settings['password'])
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise RuntimeError('L’e-mail n’a pas pu partir. Réessayez dans quelques minutes.') from error
