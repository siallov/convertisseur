import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import activation
import shared_server
import sync_membres as sm


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        state = Path(self.tmp.name)
        patches = [
            patch.object(shared_server, 'STATE', state), patch.object(shared_server, 'ACCOUNTS', state / 'accounts.json'),
            patch.object(sm, 'STATE', state), patch.object(sm, 'MANAGED', state / 'm.json'), patch.object(sm, 'PENDING', state / 'p.txt'), patch.object(sm, 'SMTP', state / 's.json'), patch.object(activation, 'FILE', state / 'a.json'),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        shared_server.store_account('andre', 'a-long-admin-password')

    def test_username_is_ascii_and_unique_by_id(self):
        self.assertEqual(sm.username(12, 'Éloïse Dupont-Martin'), 'eloise-dupont-martin-12')
        self.assertNotEqual(sm.username(1, 'Jean'), sm.username(2, 'Jean'))
        self.assertEqual(sm.username(5, '???'), 'choriste-5')

    def test_creates_then_removes_only_managed_accounts(self):
        create, remove = sm.sync([(1, 'Alice A', 'a@x.fr'), (2, 'Bob B', '')])
        self.assertEqual(sorted(create.values()), ['alice-a-1', 'bob-b-2'])
        self.assertEqual(remove, {})
        self.assertEqual(set(shared_server.accounts()), {'andre', 'alice-a-1', 'bob-b-2'})
        lines = sm.PENDING.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        token = lines[0].split('token=')[1]
        self.assertEqual(activation.lookup(token), 'alice-a-1')
        self.assertNotIn(token, sm.PENDING.parent.joinpath('a.json').read_text())
        again = sm.sync([(1, 'Alice A', 'a@x.fr'), (2, 'Bob B', '')])
        self.assertEqual(again, ({}, {}))
        sm.sync([(1, 'Alice A', 'a@x.fr')])
        self.assertEqual(set(shared_server.accounts()), {'andre', 'alice-a-1'})

    def test_empty_list_is_refused_when_accounts_exist(self):
        sm.sync([(1, 'Alice A', 'a@x.fr')])
        with self.assertRaises(SystemExit):
            sm.sync([])
        self.assertIn('alice-a-1', shared_server.accounts())

    def test_dry_run_changes_nothing(self):
        create, _ = sm.sync([(1, 'Alice A', 'a@x.fr')], dry_run=True)
        self.assertEqual(list(create.values()), ['alice-a-1'])
        self.assertEqual(set(shared_server.accounts()), {'andre'})

    def test_fetch_members_calls_api_with_basic_auth(self):
        seen = {}

        class Api(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_POST(self):
                seen['auth'] = self.headers['Authorization']
                seen['body'] = self.rfile.read(int(self.headers['Content-Length'])).decode()
                data = json.dumps({'results': [{'id': 7, 'name': 'Zoé Z', 'email': 'z@x.fr'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = HTTPServer(('127.0.0.1', 0), Api)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = {'url': f'http://127.0.0.1:{server.server_port}', 'api_id': 'sync', 'api_secret': 's', 'categories': [1]}
        self.assertEqual(sm.fetch_members(config), [(7, 'Zoé Z', 'z@x.fr')])
        self.assertTrue(seen['auth'].startswith('Basic '))
        self.assertIn('id_category+IN+%281%29', seen['body'])

    def test_mail_sent_skips_pending_file(self):
        sm.SMTP.write_text(json.dumps({'host': 'h', 'port': 465, 'user': 'u', 'password': 'p', 'from': 'u@x.fr'}))
        with patch.object(sm.smtplib, 'SMTP_SSL') as smtp:
            sm.sync([(1, 'Alice A', 'a@x.fr')])
        server = smtp.return_value.__enter__.return_value
        message = server.send_message.call_args[0][0]
        self.assertEqual(message['To'], 'a@x.fr')
        self.assertIn('/activation?token=', message.get_content())
        self.assertFalse(sm.PENDING.exists())

    def test_activation_link_is_single_use_and_sets_password(self):
        sm.sync([(1, 'Alice A', '')])
        token = sm.PENDING.read_text().split('token=')[1].split()[0]
        self.assertEqual(activation.lookup(token), 'alice-a-1')
        shared_server.store_account('alice-a-1', 'chosen-password-123')
        activation.burn(token)
        self.assertIsNone(activation.lookup(token))
        self.assertIsNone(activation.lookup('x' * 30))

    def test_expired_link_is_rejected(self):
        token = activation.create('andre')
        with patch.object(activation.time, 'time', return_value=__import__('time').time() + activation.VALIDITY + 5):
            self.assertIsNone(activation.lookup(token))


if __name__ == '__main__':
    unittest.main()
