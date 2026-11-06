"""Optional upload of finished conversions to Paheko Documents over WebDAV.

Configuration lives in private/paheko.json (mode 600), created with
`python3 setup_paheko.py`. Without it, nothing is sent.
"""
import base64
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

CONFIG = Path(__file__).resolve().parent / 'private' / 'paheko.json'


def load_config():
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
        base = urlparse(config['url'])
        if base.scheme != 'https' or not base.netloc or not config['user'] or not config['password']:
            return None
        return config
    except (OSError, ValueError, KeyError):
        return None


def _request(config, method, path, data=None, headers=None):
    url = config['url'].rstrip('/') + '/' + path
    token = base64.b64encode(f"{config['user']}:{config['password']}".encode()).decode()
    request = Request(url, data=data, method=method, headers={'Authorization': 'Basic ' + token, **(headers or {})})
    return urlopen(request, timeout=60)


def publish(files, detailed=False):
    """files: list of (display_name, Path). Returns a short French status message, or None if disabled."""
    config = load_config()
    if not config:
        return None
    folder = quote(config.get('folder', '').strip('/'), safe='/')
    prefix = folder + '/' if folder else ''
    sent, existing = [], []
    def answer(state, message):
        return {'state': state, 'message': message, 'sent': sent, 'existing': existing} if detailed else message
    try:
        if folder:
            try:
                _request(config, 'MKCOL', folder).close()
            except HTTPError as error:
                if error.code != 405:
                    raise
        for name, path in files:
            try:
                # If-None-Match avoids overwriting a document already in Paheko.
                _request(config, 'PUT', prefix + quote(name, safe=''), path.read_bytes(),
                         {'If-None-Match': '*', 'Content-Type': 'application/octet-stream'}).close()
                sent.append(name)
            except HTTPError as error:
                if error.code in (409, 412):
                    existing.append(name)
                else:
                    raise
    except (HTTPError, URLError, OSError) as error:
        code = f' (code {error.code})' if isinstance(error, HTTPError) else ''
        return answer('failed', f'Envoi vers Paheko impossible{code}.')
    if existing and not sent:
        return answer('existing', 'Déjà présent dans Paheko : aucun fichier remplacé.')
    suffix = ' Certains fichiers existaient déjà et n’ont pas été remplacés.' if existing else ''
    return answer('existing' if existing else 'sent', 'Envoyé dans Paheko Documents.' + suffix)
