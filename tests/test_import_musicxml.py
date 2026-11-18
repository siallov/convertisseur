import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import app

SCORE = '''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Basse</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions><key><fifths>1</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>F</sign><line>4</line></clef></attributes>
      <direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit><per-minute>100</per-minute></metronome></direction-type></direction>
      <note><pitch><step>G</step><octave>2</octave></pitch><duration>2</duration><type>half</type>
        <lyric><syllabic>single</syllabic><text>Le</text></lyric></note>
      <note><pitch><step>D</step><octave>3</octave></pitch><duration>2</duration><type>half</type>
        <lyric><syllabic>single</syllabic><text>duc</text></lyric></note>
    </measure>
    <measure number="2">
      <note><pitch><step>G</step><octave>2</octave></pitch><duration>4</duration><type>whole</type>
        <lyric><syllabic>single</syllabic><text>Bordeaux</text></lyric></note>
    </measure>
  </part>
</score-partwise>
'''


def compressed(xml):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr('score.xml', xml)
    return buffer.getvalue()


class MusicXMLImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patches = [patch.object(app, 'DATA', self.data), patch.object(app, 'engine_command', return_value=None)]
        for item in self.patches:
            item.start()
        app.JOBS.clear()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for item in self.patches:
            item.stop()
        app.JOBS.clear()
        self.temp.cleanup()

    def send(self, body, name='duc.musicxml', title=''):
        headers = {'Content-Type': 'application/vnd.recordare.musicxml', 'X-Filename': quote(name),
                   'X-Score-Title': quote(title), 'X-Score-Part': 'Basse'}
        with urlopen(Request(self.base + '/api/jobs/musicxml', data=body, headers=headers)) as response:
            self.assertEqual(response.status, 202)
            return json.load(response)['id']

    def test_musicxml_skips_recognition_and_waits_for_review(self):
        # Audiveris is absent (engine_command patched): an import must not need it.
        job_id = self.send(SCORE.encode(), title='Le duc de Bordeaux')
        job = app.JOBS[job_id]
        self.assertEqual(job['status'], 'review')
        self.assertTrue(job['imported'])
        self.assertEqual(job['files'], ['partition-1.mxl', 'partition-1.musicxml'])
        folder = self.data / job_id
        self.assertIn('<work-title>Le duc de Bordeaux</work-title>', (folder / 'partition-1.musicxml').read_text(encoding='utf-8'))
        with zipfile.ZipFile(folder / 'partition-1.mxl') as archive:
            self.assertIn('partition-1.musicxml', archive.namelist())
        extracted = folder / 'check.musicxml'
        app.extract_musicxml(folder / 'partition-1.mxl', extracted)
        self.assertIn(b'Bordeaux', extracted.read_bytes())
        self.assertFalse((folder / 'source.pdf').exists())
        # after a restart the import is still under review, still without PDF
        restored = app.restore_job(folder)
        self.assertEqual((restored['status'], restored.get('imported')), ('review', True))
        with urlopen(f'{self.base}/api/jobs/{job_id}/partition-1.musicxml') as response:
            self.assertIn('Le duc de Bordeaux', response.headers['Content-Disposition'])

    def test_compressed_mxl_is_accepted(self):
        job_id = self.send(compressed(SCORE), name='duc.mxl')
        self.assertIn('<score-partwise', (self.data / job_id / 'partition-1.musicxml').read_text(encoding='utf-8'))

    def test_invalid_files_are_refused_and_leave_nothing(self):
        for body in (b'<html>pas une partition</html>', b'<score-partwise', b'PK\x03\x04abime', b'%PDF-1.4'):
            with self.subTest(body=body[:12]):
                with self.assertRaises(HTTPError) as error:
                    self.send(body)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
        self.assertEqual(app.JOBS, {})
        self.assertEqual(list(self.data.iterdir()), [])

    def test_deposit_without_original_pdf(self):
        job_id = self.send(SCORE.encode(), title='Le duc de Bordeaux')
        sent = {}

        def publish(payload, detailed=False):
            sent['names'] = [name for name, _ in payload]
            return {'state': 'sent', 'message': 'Déposé.', 'sent': []}
        with patch.object(app.paheko_upload, 'publish', side_effect=publish), \
                patch.object(app.paheko_upload, 'load_config', return_value=None):
            app.publish_job(job_id)
        self.assertEqual(app.JOBS[job_id]['status'], 'done')
        self.assertFalse([name for name in sent['names'] if name.endswith('.pdf')])
        self.assertTrue([name for name in sent['names'] if name.endswith('.musicxml')])


if __name__ == '__main__':
    unittest.main()
