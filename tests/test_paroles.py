import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app
import paroles

LYRICS = ['Le', 'duc', 'de', 'Bor-', 'deaux', 'res-', 'semble', 'à', 'son', 'père']


def sample_pdf():
    """A digital score: two five-line staves and one line of lyrics under each."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for top in (100, 300):
        for line in range(5):
            y = top + line * 8
            page.draw_line((50, y), (545, y), width=0.6)
        page.draw_line((50, top), (50, top + 32), width=0.8)
        x = 70
        for word in LYRICS:
            page.insert_text((x, top + 60), word, fontsize=10, fontname='helv')
            x += 45
    page.insert_text((200, 60), 'Titre du chant', fontsize=18, fontname='tiro')
    data = doc.tobytes()
    doc.close()
    return data


@unittest.skipUnless(paroles.available(), 'PyMuPDF non installé')
class LyricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(app, 'DATA', Path(self.temp.name))
        self.patch.start()
        paroles.SESSIONS.clear()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}/api/paroles'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.patch.stop()
        paroles.SESSIONS.clear()
        self.temp.cleanup()

    def call(self, path='', data=None, content_type='application/json'):
        body = json.dumps(data).encode() if isinstance(data, dict) else data
        headers = {'Content-Type': content_type, 'X-Filename': 'chant%20test.pdf'}
        return urlopen(Request(self.base + path, data=body, headers=headers))

    def analysed(self):
        with self.call('', sample_pdf(), 'application/pdf') as response:
            session_id = json.load(response)['id']
        for _ in range(100):
            with self.call(f'/{session_id}') as response:
                info = json.load(response)
            if info['state'] != 'analysing':
                return session_id, info
            time.sleep(0.05)
        self.fail('analyse trop longue')

    def test_extracts_text_and_removes_lyrics(self):
        session_id, info = self.analysed()
        self.assertEqual(info['state'], 'ready')
        self.assertEqual(len(info['pages']), 1)
        selection = {'styles': [s['id'] for s in info['styles'] if s['selected']]}
        self.assertEqual(len(selection['styles']), 1)
        with self.call(f'/{session_id}/calcul', selection) as response:
            result = json.load(response)
        self.assertEqual(len(result['lyrics']), 2 * len(LYRICS))
        self.assertIn('Le duc de Bor', result['text'])
        self.assertNotIn('Titre du chant', result['text'])
        with self.call(f'/{session_id}/page/0?zoom=1') as response:
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertTrue(response.read().startswith(b'\x89PNG'))
        with self.call(f'/{session_id}/apercu/0?zoom=1', selection) as response:
            self.assertTrue(response.read().startswith(b'\x89PNG'))
        with self.call(f'/{session_id}/pdf', selection) as response:
            pdf = response.read()
            self.assertEqual(response.headers['X-Remaining'], '0')
            self.assertIn('chant test_sans_paroles.pdf', response.headers['Content-Disposition'])
        import pymupdf
        with pymupdf.open(stream=pdf, filetype='pdf') as doc:
            text = doc[0].get_text()
        self.assertIn('Titre du chant', text)
        self.assertNotIn('semble', text)

    def test_clicked_word_and_margin_are_respected(self):
        session_id, info = self.analysed()
        style = [s['id'] for s in info['styles'] if s['selected']]
        title = next(s for s in info['spans'] if s['text'] == 'Titre du chant')
        word = next(s for s in info['spans'] if s['text'] == 'semble')
        selection = {'styles': style, 'overrides': {str(title['id']): True, str(word['id']): False}}
        with self.call(f'/{session_id}/calcul', selection) as response:
            result = json.load(response)
        self.assertIn(title['id'], result['lyrics'])
        self.assertNotIn(word['id'], result['lyrics'])
        with self.assertRaises(HTTPError) as error:
            self.call(f'/{session_id}/pdf', {'styles': []})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_invalid_input_and_other_owner(self):
        for body in (b'pas un pdf', b''):
            with self.assertRaises(HTTPError) as error:
                self.call('', body, 'application/pdf')
            self.assertIn(error.exception.code, (400, 413))
            error.exception.close()
        session_id, _ = self.analysed()
        self.assertIsNone(paroles.get(session_id, owner='bob'))
        with self.assertRaises(HTTPError) as error:
            self.call('/' + 'f' * 32)
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.call(f'/{session_id}/page/9')
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_old_sessions_are_removed(self):
        session_id, _ = self.analysed()
        paroles.SESSIONS[session_id]['used'] = 0
        paroles.cleanup(app.DATA)
        self.assertNotIn(session_id, paroles.SESSIONS)
        self.assertFalse(paroles.folder(app.DATA, session_id).exists())


class LyricsUnavailableTests(unittest.TestCase):
    def test_tab_reports_missing_libraries(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(paroles, 'pc', None):
                with urlopen(base + '/api/paroles/statut') as response:
                    self.assertFalse(json.load(response)['available'])
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/paroles', data=b'%PDF-1.4', headers={'Content-Type': 'application/pdf'}))
                self.assertEqual(error.exception.code, 503)
                error.exception.close()
            with urlopen(base + '/paroles') as response:
                self.assertIn('Extraire les paroles', response.read().decode())
        finally:
            server.shutdown()
            server.server_close()
