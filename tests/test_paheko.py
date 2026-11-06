import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import paheko_upload


class FakeDav(BaseHTTPRequestHandler):
    store = {}
    log = []

    def log_message(self, *args):
        pass

    def reply(self, code):
        self.send_response(code)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_MKCOL(self):
        self.log.append(('MKCOL', self.path, self.headers.get('Authorization')))
        self.reply(405 if self.path in self.store else 201)

    def do_PUT(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        if self.path in self.store:
            return self.reply(412)
        self.store[self.path] = body
        self.reply(201)


class PahekoUploadTest(unittest.TestCase):
    def setUp(self):
        FakeDav.store, FakeDav.log = {}, []
        self.server = HTTPServer(('127.0.0.1', 0), FakeDav)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / 'x.musicxml'
        self.file.write_text('<score-partwise/>')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def config(self, url=None):
        path = Path(self.temp.name) / 'paheko.json'
        path.write_text(json.dumps({'url': url or f'https://127.0.0.1:{self.server.server_port}/dav/documents/',
                                    'folder': 'Partitions', 'user': 'bot', 'password': 'secret'}))
        return patch.object(paheko_upload, 'CONFIG', path)

    def test_disabled_without_configuration(self):
        with patch.object(paheko_upload, 'CONFIG', Path(self.temp.name) / 'absent.json'):
            self.assertIsNone(paheko_upload.publish([('a.musicxml', self.file)]))

    def test_plain_http_refused(self):
        with self.config('http://example.org/dav/'):
            self.assertIsNone(paheko_upload.load_config())

    def test_upload_and_no_overwrite(self):
        def fake_request(config, method, path, data=None, headers=None):
            from urllib.request import Request, urlopen
            url = f'http://127.0.0.1:{self.server.server_port}/dav/documents/' + path
            return urlopen(Request(url, data=data, method=method, headers=headers or {}), timeout=10)
        with self.config(), patch.object(paheko_upload, '_request', fake_request):
            self.assertEqual(paheko_upload.publish([("J'aime le vin.musicxml", self.file)]), 'Envoyé dans Paheko Documents.')
            self.assertIn("/dav/documents/Partitions/J%27aime%20le%20vin.musicxml", FakeDav.store)
            self.assertIn('Déjà présent', paheko_upload.publish([("J'aime le vin.musicxml", self.file)]))


if __name__ == '__main__':
    unittest.main()
