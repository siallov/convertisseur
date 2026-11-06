"""One-time activation links: a new choir member chooses their own password."""
import hashlib
import json
import re
import secrets
import time
from pathlib import Path

FILE = Path(__file__).resolve().parent / 'private' / 'activations.json'
VALIDITY = 48 * 3600
TOKEN = re.compile(r'[A-Za-z0-9_-]{20,80}')


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _load():
    try:
        data = json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if v['expires'] > now}


def _save(data):
    FILE.parent.mkdir(mode=0o700, exist_ok=True)
    temporary = FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.chmod(0o600)
    temporary.replace(FILE)


def create(user):
    """Returns a new token for the user (only its hash is stored); older links of that user are revoked."""
    token = secrets.token_urlsafe(32)
    data = {k: v for k, v in _load().items() if v['user'] != user}
    data[_digest(token)] = {'user': user, 'expires': time.time() + VALIDITY}
    _save(data)
    return token


def lookup(token):
    if not TOKEN.fullmatch(token or ''):
        return None
    entry = _load().get(_digest(token))
    return entry['user'] if entry else None


def burn(token):
    data = _load()
    data.pop(_digest(token), None)
    _save(data)
