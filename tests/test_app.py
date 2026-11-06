import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import zipfile
import app


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = patch.object(app, 'DATA', self.data)
        self.patch.start()
        self.publish_patch = patch.object(app.paheko_upload, "publish", return_value=None)
        self.publish_patch.start()
        self.resolution_patch = patch.object(app, 'pdf_resolution_options', return_value=[])
        self.resolution_patch.start()
        app.JOBS.clear()

    def tearDown(self):
        self.resolution_patch.stop()
        self.publish_patch.stop()
        self.patch.stop()
        self.temp.cleanup()

    def test_large_pdf_resolution(self):
        self.resolution_patch.stop()
        from types import SimpleNamespace
        result = SimpleNamespace(returncode=0, stdout='Page    1 size:  2550 x 3507 pts')
        with patch.object(app.shutil, 'which', return_value='/usr/bin/pdfinfo'), patch.object(app.subprocess, 'run', return_value=result):
            self.assertEqual(app.pdf_resolution_options(Path('score.pdf'))[-1], 'org.audiveris.omr.image.ImageLoading.pdfResolution=102')
            result.stdout = 'Page    1 size: 595 x 842 pts'
            self.assertEqual(app.pdf_resolution_options(Path('score.pdf')), [])

    def test_conversion_and_xml_extraction(self):
        job_id = 'a' * 32
        folder = self.data / job_id
        folder.mkdir()
        (folder / 'source.pdf').write_bytes(b'%PDF-1.4\n')
        app.JOBS[job_id] = {'status': 'queued', 'files': []}
        def engine(command, **kwargs):
            self.assertIn('-export', command)
            self.assertIn('-transcribe', command)
            output = folder / 'output' / 'source'
            output.mkdir(parents=True)
            with zipfile.ZipFile(output / 'source.mxl', 'w') as archive:
                archive.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                archive.writestr('score.xml', '<score-partwise version="4.0"><part-list/></score-partwise>')
            return type('Result', (), {'returncode': 0})()
        with patch.object(app.subprocess, 'run', side_effect=engine):
            app.convert(job_id, ['audiveris'])
        self.assertEqual(app.JOBS[job_id]['status'], 'review')
        self.assertEqual(len(app.JOBS[job_id]['files']), 2)
        self.assertIn(b'score-partwise', (folder / 'partition-1.musicxml').read_bytes())

    def test_failure_is_reported(self):
        job_id = 'b' * 32
        (self.data / job_id).mkdir()
        app.JOBS[job_id] = {'status': 'queued', 'files': []}
        with patch.object(app.subprocess, 'run', return_value=type('Result', (), {'returncode': 1})()):
            app.convert(job_id, ['audiveris'])
        self.assertEqual(app.JOBS[job_id]['status'], 'error')
        self.assertIn('journal', app.JOBS[job_id]['message'])

    def test_http_validation_and_interface(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base) as response:
                self.assertIn('Vos partitions'.encode(), response.read())
            with patch.object(app, 'engine_command', return_value=None):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/jobs', data=b'%PDF-1.4'))
                self.assertEqual(error.exception.code, 503)
                error.exception.close()
            with patch.object(app, 'engine_command', return_value=['audiveris']):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + '/api/jobs', data=b'not a pdf'))
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/api/jobs/' + 'a'*32 + '/../app.py')
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

if __name__ == '__main__':
    unittest.main()
