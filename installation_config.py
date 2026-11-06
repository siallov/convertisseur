"""Portable installation parameters. Secrets stay in the existing private DAV file."""
import json
import os
import re
import tempfile
from urllib.parse import urlparse

import paheko_upload


def raw():
    try:
        value = json.loads(paheko_upload.CONFIG.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def https_url(value, origin=False):
    if not isinstance(value, str):
        raise TypeError('Adresse HTTPS invalide.')
    u = urlparse(value.strip())
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or any(c.isspace() for c in value):
        raise ValueError('Utilisez une adresse HTTPS sans identifiants, paramètres ni fragment.')
    if origin and u.path.strip('/'):
        raise ValueError('Indiquez seulement le domaine HTTPS, sans chemin.')
    try:
        if u.port is not None and not 0 <= u.port <= 65535:
            raise ValueError('Port invalide.')
    except ValueError:
        raise ValueError('Port invalide.')
    return value.strip().rstrip('/')


def public():
    c = raw()
    u = urlparse(c.get('url', ''))
    base = f'{u.scheme}://{u.netloc}' if u.scheme == 'https' and u.netloc else ''
    return {'association': c.get('association', 'Ma chorale'), 'paheko_origin':base,
            'module_path':c.get('module_path', '/m/chorale/'),
            'converter_url':c.get('public_url', ''), 'folder':c.get('folder', 'Partitions')}


def administrative():
    c = raw()
    return {**public(), 'webdav_url':c.get('url',''), 'webdav_user':c.get('user',''), 'password_saved':bool(c.get('password'))}


def save(values):
    if not isinstance(values, dict):
        raise TypeError('Configuration invalide.')
    old = raw()
    url = https_url(values.get('webdav_url', '')) + '/'
    converter = https_url(values.get('converter_url', ''), origin=True)
    folder = values.get('folder','').strip().strip('/')
    if not folder or any(part in ('', '.', '..') or '\\' in part or any(ord(c)<32 for c in part) for part in folder.split('/')):
        raise ValueError('Dossier de partitions invalide (exemple : Partitions).')
    module = values.get('module_path','').strip()
    if not re.fullmatch(r'/m/[a-zA-Z0-9_-]+/', module):
        raise ValueError('Chemin du module invalide (exemple : /m/chorale/).')
    name = values.get('association','').strip()
    user = values.get('webdav_user','').strip()
    password = values.get('webdav_password','')
    if not isinstance(password, str) or not user or ':' in user or not name or len(name)>160:
        raise ValueError('Nom de chorale ou identifiant WebDAV invalide.')
    if not password:
        if url.rstrip('/') != old.get('url','').rstrip('/') or user != old.get('user'):
            raise ValueError('Saisissez le mot de passe pour cette nouvelle destination ou ce nouveau compte.')
        password = old.get('password','')
    if not password:
        raise ValueError('Le mot de passe WebDAV est nécessaire.')
    data = dict(old, url=url, folder=folder, user=user, password=password, association=name, module_path=module, public_url=converter)
    path = paheko_upload.CONFIG
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.installation-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return administrative()
