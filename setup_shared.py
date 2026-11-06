#!/usr/bin/env python3
"""One-time, loopback-only initial setup. Never publish this server."""
import hashlib
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs
import shared_server as shared

PAGE = '''<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Configurer le partage des partitions</title><style>body{font:16px system-ui;max-width:620px;margin:50px auto;padding:24px;color:#243e32;background:#f6f7f3}input,textarea,button{box-sizing:border-box;width:100%;padding:12px;margin:8px 0 22px;font:inherit}button{background:#3d5c3d;color:white;border:0;border-radius:6px}small{display:block;color:#66735e;line-height:1.5}</style><h1>Votre convertisseur privé</h1><p>Ces informations sont enregistrées uniquement sur cet ordinateur. Le mot de passe sera conservé sous forme d’empreinte.</p><form method="post" autocomplete="off"><label>Votre identifiant<input name="username" value="andre" required pattern="[A-Za-z0-9_.-]{3,40}" autocomplete="username"></label><label>Votre mot de passe<input name="password" type="password" minlength="12" required autocomplete="new-password"></label><label>Confirmer le mot de passe<input name="confirmation" type="password" minlength="12" required autocomplete="new-password"></label><label>Commande du tunnel Cloudflare<textarea name="tunnel" rows="4" required placeholder="Collez ici la commande cloudflared copiée depuis Cloudflare"></textarea></label><small>Dans l’onglet Cloudflare, utilisez le bouton de copie de la commande « Or, run tunnel (manual) », puis collez-la ici. La commande complète ou son jeton seul est accepté.</small><button>Enregistrer sur mon PC</button></form></html>'''


class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def response(self, code, text):
        content=text.encode()
        self.send_response(code)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path != self.server.setup_path:
            return self.response(404,'Page introuvable.')
        self.response(200,PAGE)

    def do_POST(self):
        if self.path != self.server.setup_path or shared.ACCOUNTS.exists():
            return self.response(403,'Configuration initiale déjà effectuée ou lien invalide.')
        if self.headers.get('Origin') != 'http://127.0.0.1:8767':
            return self.response(403,'Origine refusée.')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length < 16384:
                raise ValueError()
            values=parse_qs(self.rfile.read(length).decode(),strict_parsing=True)
            name=values['username'][0]
            password=values['password'][0]
            if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}',name) or len(password)<12 or password!=values['confirmation'][0]:
                return self.response(400,'Identifiant invalide ou mots de passe différents. Revenez à la page précédente pour corriger.')
            token=values['tunnel'][0].strip().split()[-1]
            if not re.fullmatch(r'[A-Za-z0-9_=-]{50,4096}',token):
                return self.response(400,'Le jeton est invalide ou masqué. Copiez la commande avec le bouton de copie Cloudflare, puis revenez à la page précédente.')
            os.umask(0o077)
            shared.STATE.mkdir(mode=0o700,exist_ok=True)
            salt=secrets.token_hex(16)
            digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
            shared.ACCOUNTS.write_text(json.dumps({name:{'salt':salt,'hash':digest}}))
            shared.ACCOUNTS.chmod(0o600)
            tokenfile=shared.STATE/'tunnel.token'
            tokenfile.write_text(token)
            tokenfile.chmod(0o600)
            self.response(200,'<!doctype html><meta charset="utf-8"><h1>Configuration enregistrée</h1><p>Votre compte et le jeton sont enregistrés sur ce PC. Revenez dans le chat et indiquez « enregistré » pour poursuivre.</p>')
            threading.Thread(target=self.server.shutdown,daemon=True).start()
        except (ValueError,KeyError,UnicodeError):
            self.response(400,'Formulaire invalide. Revenez à la page précédente.')

if __name__=='__main__':
    if shared.ACCOUNTS.exists():
        raise SystemExit('Un compte existe déjà. Utilisez shared_server.py --add-user pour gérer les comptes.')
    server=HTTPServer(('127.0.0.1',8767),SetupHandler)
    server.setup_path='/'+secrets.token_urlsafe(24)
    server.timeout=60
    print('http://127.0.0.1:8767'+server.setup_path,flush=True)
    server.serve_forever()
