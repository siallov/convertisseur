#!/usr/bin/env python3
"""Authenticated multi-user entry point; separate from local conversion history."""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import threading
import time
import zipfile
from http.cookies import CookieError, SimpleCookie
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import activation
import app
import installation_config
import pupitres

STATE = app.ROOT / 'private'
ACCOUNTS = STATE / 'accounts.json'
ADMINS = STATE / 'administrators.json'
RETENTION = 24 * 3600
# The tunnel forwards a local Host header, so the public origin is accepted explicitly.
try:
    PUBLIC_URL = json.loads((STATE / 'smtp.json').read_text()).get('public_url', '').rstrip('/')
except (OSError, ValueError):
    PUBLIC_URL = ''
PUBLIC_URL = PUBLIC_URL or ''
LOGGER = logging.getLogger(__name__)


def accounts():
    return json.loads(ACCOUNTS.read_text()) if ACCOUNTS.exists() else {}


def add_user(name):
    if not app.re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}', name):
        raise SystemExit('Identifiant : 3 à 40 lettres, chiffres, points, tirets.')
    password = getpass.getpass('Mot de passe (12 caractères minimum) : ')
    if len(password) < 12 or password != getpass.getpass('Confirmer le mot de passe : '):
        raise SystemExit('Mot de passe trop court ou confirmation différente.')
    store_account(name, password)
    print('Compte enregistré. Aucun mot de passe en clair conservé.')


def save_accounts(users):
    STATE.mkdir(mode=0o700, exist_ok=True)
    temporary = ACCOUNTS.with_suffix('.tmp')
    temporary.write_text(json.dumps(users))
    temporary.chmod(0o600)
    temporary.replace(ACCOUNTS)


def store_account(name, password):
    users = accounts()
    salt = secrets.token_hex(16)
    users[name] = {'salt': salt, 'hash': hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()}
    save_accounts(users)


def remove_account(name):
    users = accounts()
    if users.pop(name, None) is not None:
        save_accounts(users)


def cleanup():
    with app.LOCK:
        expired = [key for key, job in app.JOBS.items() if job['status'] not in ('running', 'queued', 'publishing') and time.time() - job.get('created', time.time()) > RETENTION]
        for key in expired:
            shutil.rmtree(app.DATA / key)
            del app.JOBS[key]


class SharedHandler(app.Handler):
    sessions: ClassVar[dict[str, tuple[str, float]]] = {}
    sessions_lock = threading.Lock()
    attempts: ClassVar[dict[str, tuple[float, int]]] = {}
    attempts_lock = threading.Lock()

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        allowed_origin = installation_config.public()['paheko_origin']
        if allowed_origin and getattr(self, 'send_header_origin', '') == allowed_origin:
            self.send_header('Access-Control-Allow-Origin', allowed_origin)
            self.send_header('Vary', 'Origin')
        super().end_headers()

    def log_message(self, format, *args):
        # Never log URLs, credentials, filenames or user identities.
        pass

    def login_page(self):
        body = (app.ROOT / 'static' / 'login.html').read_bytes()
        self.send_response(401)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def login(self):
        origin = self.headers.get('Origin')
        if origin and not self.origin_allowed(origin):
            return self.json_response(403, {'error': 'Origine refusée.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length < 4096:
                raise ValueError()
            fields = json.loads(self.rfile.read(length))
            name, password = fields['username'], fields['password']
            if not isinstance(name, str) or not isinstance(password, str) or ':' in name:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            return self.json_response(400, {'error': 'Saisie invalide.'})
        if 'Authorization' in self.headers:
            del self.headers['Authorization']
        self.headers['Authorization'] = 'Basic ' + base64.b64encode((name + ':' + password).encode()).decode()
        if not self.authenticate(use_session=False):
            return
        token = secrets.token_urlsafe(32)
        with self.sessions_lock:
            type(self).sessions = {k:v for k,v in self.sessions.items() if v[1] > time.time()}
            self.sessions[token] = (self.user, time.time() + 12 * 3600)
        self.send_response(200)
        self.send_header('Set-Cookie', '__Host-omr_session=' + token + '; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=43200')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', '11')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def authenticate(self, use_session=True):
        if use_session:
            try:
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                token = cookie.get('__Host-omr_session')
                with self.sessions_lock:
                    session = self.sessions.get(token.value) if token else None
                if session and session[1] > time.time() and session[0] in accounts():
                    self.user = session[0]
                    return True
            except CookieError:
                LOGGER.debug('Ignoring malformed session cookie')
        # Loopback is not trusted: local requests require authentication too.
        identity = self.headers.get('CF-Connecting-IP', self.client_address[0])
        now = time.monotonic()
        with self.attempts_lock:
            type(self).attempts = {k:v for k,v in self.attempts.items() if now-v[0] < 300}
            count = self.attempts.get(identity, (now, 0))[1]
        if count >= 10:
            self.json_response(429, {'error': 'Trop de tentatives. Réessayez dans cinq minutes.'})
            return False
        try:
            kind, encoded = self.headers.get('Authorization', '').split(' ', 1)
            if kind.lower() != 'basic':
                raise ValueError()
            name, password = base64.b64decode(encoded, validate=True).decode().split(':', 1)
            user = accounts().get(name)
            if not user:
                raise ValueError()
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(user['salt']), 600000).hex()
            if not hmac.compare_digest(digest, user['hash']):
                raise ValueError()
        except (ValueError, UnicodeError):
            if self.headers.get('Authorization'):
                with self.attempts_lock:
                    self.attempts[identity] = (now, count + 1)
            if self.command == 'GET' and urlparse(self.path).path == '/':
                self.login_page()
            else:
                self.json_response(401, {'error': 'Identifiant ou mot de passe incorrect, ou session expirée.'})
            return False
        self.user = name
        return True

    def activation_page(self, token, message='', code=200):
        form = ''
        if token:
            form = (f'<form method="post" action="/activation"><input type="hidden" name="token" value="{token}">'
                    '<p><label>Nouveau mot de passe (12 caractères minimum)<br><input type="password" name="password" minlength="12" required autocomplete="new-password"></label></p>'
                    '<p><label>Confirmer<br><input type="password" name="confirm" minlength="12" required autocomplete="new-password"></label></p>'
                    '<p><button>Enregistrer</button></p></form>')
        body = ('<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Activer mon compte</title><body style="font-family:sans-serif;max-width:30em;margin:2em auto;padding:0 1em">'
                f'<h1>Activer mon compte</h1><p>{message}</p>{form}</body></html>').encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def origin_allowed(self, origin):
        host = self.headers.get('Host', '')
        allowed = {'http://' + host, 'https://' + host, installation_config.public()['converter_url'] or PUBLIC_URL}
        return origin in allowed

    def throttled(self):
        identity = self.headers.get('CF-Connecting-IP', self.client_address[0])
        now = time.monotonic()
        with self.attempts_lock:
            type(self).attempts = {k:v for k,v in self.attempts.items() if now-v[0] < 300}
            return self.attempts.get(identity, (now, 0))[1] >= 10, identity, now

    def count_failure(self, identity, now):
        with self.attempts_lock:
            self.attempts[identity] = (now, self.attempts.get(identity, (now, 0))[1] + 1)

    def handle_activation(self, post):
        blocked, identity, now = self.throttled()
        if blocked:
            return self.activation_page('', 'Trop de tentatives. Réessayez dans cinq minutes.', 429)
        invalid = 'Ce lien est invalide ou expiré. Demandez-en un nouveau à l’administrateur.'
        if not post:
            token = parse_qs(urlparse(self.path).query).get('token', [''])[0]
            if activation.lookup(token):
                return self.activation_page(token, 'Choisissez votre mot de passe.')
            self.count_failure(identity, now)
            return self.activation_page('', invalid, 404)
        origin = self.headers.get('Origin')
        if origin and not self.origin_allowed(origin):
            return self.activation_page('', 'Origine refusée.', 403)
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length < 4096:
            return self.activation_page('', 'Requête invalide.', 400)
        fields = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))
        token, password, confirm = (fields.get(k, [''])[0] for k in ('token', 'password', 'confirm'))
        user = activation.lookup(token)
        if user not in accounts():
            user = None
        if not user:
            self.count_failure(identity, now)
            return self.activation_page('', invalid, 404)
        if len(password) < 12 or password != confirm:
            return self.activation_page(token, 'Mot de passe trop court (12 caractères minimum) ou confirmation différente.', 400)
        store_account(user, password)
        activation.burn(token)
        return self.activation_page('', f'Compte activé. Votre identifiant est : <b>{user}</b>. Vous pouvez maintenant ouvrir le convertisseur.')

    def is_admin(self):
        try:
            return self.user in json.loads(ADMINS.read_text())
        except (OSError, ValueError, TypeError):
            return False

    def dashboard(self):
        with app.LOCK:
            jobs = [dict(id=k, **v) for k,v in app.JOBS.items()]
        storage = 0
        for file in app.DATA.rglob('*'):
            try:
                if file.is_file(): storage += file.stat().st_size
            except FileNotFoundError:
                pass
        fields = ('id', 'name', 'title', 'owner', 'status', 'message', 'created', 'error_code')
        clean = lambda job: {k:job.get(k) for k in fields}
        recent = sorted(jobs, key=lambda j:j.get('created',0), reverse=True)
        return self.json_response(200, {
            'storage_bytes': storage, 'storage_limit': 1024**3, 'retention_hours':24,
            'counts': {state:sum(j.get('status')==state for j in jobs) for state in ('queued','running','review','publishing','done','error')},
            'queue':[clean(j) for j in recent if j.get('status') in ('queued','running','publishing')],
            'failures':[clean(j) for j in recent if j.get('status')=='error' or j.get('error_code')=='upload_failed'][:20]})

    def do_GET(self):
        if urlparse(self.path).path == '/api/status':
            self.send_header_origin = self.headers.get('Origin')
            return self.json_response(200, {'available': bool(app.engine_command())})
        if urlparse(self.path).path == '/activation':
            return self.handle_activation(False)
        if not self.authenticate():
            return
        cleanup()
        path = urlparse(self.path).path
        match = app.re.fullmatch(r'/api/jobs/([a-f0-9]{32})/pupitres', path)
        if match:
            with app.LOCK:
                job = app.JOBS.get(match[1])
                if not job or job.get('owner') != self.user:
                    return self.json_response(404, {'error':'Conversion introuvable.'})
                filename = parse_qs(urlparse(self.path).query).get('file',[''])[0]
                if filename not in job.get('files',[]):
                    return self.json_response(404, {'error':'Partition introuvable.'})
                try:return self.json_response(200,pupitres.describe(app.DATA/match[1],filename))
                except (ValueError,OSError):return self.json_response(400,{'error':'Impossible de lire les lignes de cette partition.'})
        if path == '/api/admin/settings':
            if not self.is_admin():
                return self.json_response(403, {'error':'Accès réservé à l’administrateur.'})
            config = installation_config.administrative()
            config['converter_url'] = config['converter_url'] or PUBLIC_URL
            return self.json_response(200, config)
        if path == '/api/me':
            return self.json_response(200, {'admin':self.is_admin()})
        if path in ('/admin', '/api/admin'):
            if not self.is_admin():
                return self.json_response(403, {'error':'Accès réservé à l’administrateur.'})
            if path == '/api/admin': return self.dashboard()
            return self.file_response(app.ROOT / 'static' / 'admin.html', 'text/html; charset=utf-8')
        if path == '/':
            return self.file_response(app.ROOT / 'static' / 'shared-index.html', 'text/html; charset=utf-8')
        if path == '/api/jobs':
            with app.LOCK:
                jobs = [dict(id=k, **v) for k,v in reversed(list(app.JOBS.items())) if v.get('owner') == self.user]
            return self.json_response(200, jobs)
        match = app.re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/([a-z0-9.-]+))?', path)
        if match:
            with app.LOCK:
                owned = app.JOBS.get(match[1], {}).get('owner') == self.user
            if not owned:
                return self.json_response(404, {'error': 'Conversion introuvable.'})
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path == '/login':
            return self.login()
        if urlparse(self.path).path == '/activation':
            return self.handle_activation(True)
        if not self.authenticate():
            return
        origin = self.headers.get('Origin')
        if origin and not self.origin_allowed(origin):
            return self.json_response(403, {'error': 'Origine refusée.'})
        if urlparse(self.path).path == '/api/admin/settings':
            if not self.is_admin():
                return self.json_response(403, {'error':'Accès réservé à l’administrateur.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 8192:
                    raise ValueError('Configuration trop grande ou vide.')
                values = json.loads(self.rfile.read(length))
                with app.LOCK:
                    if any(j.get('status') in ('queued','running','publishing','review') for j in app.JOBS.values()):
                        return self.json_response(409, {'error':'Terminez les conversions en attente et leur validation avant de changer la destination.'})
                    result = installation_config.save(values)
                return self.json_response(200, result)
            except (ValueError, TypeError, AttributeError):
                return self.json_response(400, {'error':'Paramètres invalides. Vérifiez HTTPS, le dossier, /m/nom_du_module/ et le mot de passe pour une nouvelle destination.'})
            except OSError:
                return self.json_response(500, {'error':'Impossible d’enregistrer la configuration privée.'})
        match = app.re.fullmatch(r'/api/jobs/([a-f0-9]{32})/pupitres', urlparse(self.path).path)
        if match:
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=524288:raise ValueError('Paramètres trop grands ou vides.')
                values=json.loads(self.rfile.read(length))
                if not isinstance(values,dict):raise TypeError('Paramètres invalides.')
                with app.LOCK:
                    job=app.JOBS.get(match[1])
                    if not job or job.get('owner')!=self.user:
                        return self.json_response(404,{'error':'Conversion introuvable.'})
                    if job.get('status')!='review' and not (job.get('status')=='done' and job.get('upload')=='failed'):
                        return self.json_response(409,{'error':'Les pupitres ne peuvent être modifiés que pendant la vérification, avant le dépôt.'})
                    filename=values.get('file','')
                    if filename not in job.get('files',[]):raise ValueError('Partition introuvable.')
                    result=pupitres.save(app.DATA/match[1],filename,values.get('labels'),values.get('corrections'))
                return self.json_response(200,result)
            except (ValueError,TypeError,KeyError,StopIteration,zipfile.BadZipFile,pupitres.ET.ParseError):
                return self.json_response(400,{'error':'Pupitres invalides. Renseignez chaque nom (80 caractères maximum) et vérifiez les lignes reconnues.'})
            except OSError:
                return self.json_response(500,{'error':'Impossible d’enregistrer les pupitres. Les résultats précédents restent disponibles.'})
        match = app.re.fullmatch(r'/api/jobs/([a-f0-9]{32})/publish', urlparse(self.path).path)
        if match:
            with app.LOCK:
                job = app.JOBS.get(match[1])
                if not job or job.get('owner') != self.user:
                    return self.json_response(404, {'error':'Conversion introuvable.'})
                if job.get('status') == 'publishing':
                    return self.json_response(200, {'status':'publishing'})
                if job.get('status') != 'review' and not (job.get('status') == 'done' and job.get('upload') == 'failed'):
                    return self.json_response(409, {'error':'Cette conversion ne peut pas être déposée à nouveau.'})
                job.update(status='publishing', message='Dépôt dans Paheko en cours…')
            app.POOL.submit(app.publish_job, match[1])
            return self.json_response(200, {'status':'publishing'})
        cleanup()
        if sum(p.stat().st_size for p in app.DATA.rglob('*') if p.is_file()) > 1024**3:
            return self.json_response(507, {'error': 'Stockage temporaire plein. Réessayez après le nettoyage.'})
        # Parent validates the same origin; HTTPS is terminated by the tunnel.
        if origin:
            del self.headers['Origin']
        return super().do_POST()

    def json_response(self, code, value):
        if code == 202 and isinstance(value, dict) and 'id' in value:
            key = value['id']
            with app.LOCK:
                app.JOBS[key].update(owner=self.user, created=time.time())
                (app.DATA / key / 'metadata.json').write_text(json.dumps({'owner': self.user, 'created': app.JOBS[key]['created']}))
        return super().json_response(code, value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--add-user')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if args.add_user:
        return add_user(args.add_user)
    if not accounts():
        raise SystemExit('Créez d’abord un compte : python3 shared_server.py --add-user andre')
    os.umask(0o077)
    app.DATA = STATE / 'jobs'
    app.DATA.mkdir(parents=True, exist_ok=True)
    app.JOBS.clear()
    for folder in app.DATA.iterdir():
        meta = folder / 'metadata.json'
        if not app.re.fullmatch('[a-f0-9]{32}', folder.name) or not meta.is_file():
            continue
        data = json.loads(meta.read_text())
        files = sorted(p.name for p in folder.iterdir() if p.suffix in ('.musicxml', '.mxl'))
        app.JOBS[folder.name] = {**data, 'name': app.stored_name(folder), 'status': 'done' if files else 'error', 'message': 'Résultats restaurés.' if files else 'Conversion interrompue.', 'files': files, **app.job_details(folder)}
    cleanup()
    def janitor():
        while True:
            time.sleep(60)
            cleanup()
    threading.Thread(target=janitor, daemon=True).start()
    print(f'Service partagé protégé : http://127.0.0.1:{args.port}', flush=True)
    app.ThreadingHTTPServer(('127.0.0.1', args.port), SharedHandler).serve_forever()

if __name__ == '__main__':
    main()
