#!/usr/bin/env python3
"""Assistant local pour une nouvelle installation ; secrets saisis hors du chat."""
import getpass
import json
import sys
import base64
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import installation_config as settings


def main():
    old = settings.administrative()
    if not settings.raw():
        try:
            hint = json.loads((settings.paheko_upload.CONFIG.parent / 'installation-a-completer.json').read_text())
            for key in ['association','converter_url','webdav_url','folder','module_path']:
                if key in hint:old[key]=hint[key]
        except (OSError,ValueError):
            pass
    def ask(label, key, default=''):
        value = old.get(key) or default
        return input(f'{label}' + (f' [{value}]' if value else '') + ' : ').strip() or value
    values = {
        'association':ask('Nom de la chorale','association','Ma chorale'),
        'converter_url':ask('Adresse HTTPS du convertisseur','converter_url'),
        'webdav_url':ask('Adresse WebDAV (https://association.paheko.cloud/dav/documents/)','webdav_url'),
        'folder':ask('Dossier dans Documents','folder','Partitions'),
        'module_path':ask('Chemin du module','module_path','/m/chorale/'),
        'webdav_user':ask('Identifiant du compte Paheko dédié','webdav_user'),
        'webdav_password':getpass.getpass('Mot de passe WebDAV (vide = conserver) : ')}
    try:
        url = settings.https_url(values['webdav_url'])
        old_private = settings.raw()
        password = values['webdav_password']
        if not password:
            if url.rstrip('/') != old_private.get('url','').rstrip('/') or values['webdav_user'] != old_private.get('user'):
                raise ValueError('Mot de passe nécessaire pour la nouvelle destination.')
            password = old_private.get('password','')
        auth = base64.b64encode((values['webdav_user']+':'+password).encode()).decode()
        with urlopen(Request(url, method='PROPFIND',headers={'Authorization':'Basic '+auth,'Depth':'0'}),timeout=30):pass
        settings.save(values)
    except (ValueError, HTTPError, URLError, OSError):
        sys.exit('Connexion ou paramètres invalides. Aucun changement enregistré.')
    print('Connexion vérifiée et paramètres enregistrés. Aucun redémarrage nécessaire pour ces paramètres.')


if __name__ == '__main__':
    main()
