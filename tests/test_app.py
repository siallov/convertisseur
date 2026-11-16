import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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

    def test_retry_requeues_failed_job_and_clears_old_results(self):
        job_id = 'b' * 32
        folder = self.data / job_id
        (folder / 'output').mkdir(parents=True)
        (folder / 'source.pdf').write_bytes(b'%PDF-1.4\n')
        (folder / 'partition-1.mxl').write_bytes(b'old')
        (folder / 'result.json').write_text('{}')
        app.JOBS[job_id] = {'status': 'error', 'files': ['partition-1.mxl'], 'action': 'x', 'error_code': 'recognition'}
        with patch.object(app, 'engine_command', return_value=['audiveris']), patch.object(app.POOL, 'submit') as submit:
            self.assertEqual(app.retry_job(job_id)[0], 202)
            submit.assert_called_once_with(app.convert, job_id, ['audiveris'])
            self.assertEqual(app.retry_job(job_id)[0], 409)  # already queued
            self.assertEqual(app.retry_job('c' * 32)[0], 404)
        self.assertEqual(app.JOBS[job_id]['status'], 'queued')
        self.assertEqual(app.JOBS[job_id]['files'], [])
        self.assertFalse((folder / 'partition-1.mxl').exists() or (folder / 'result.json').exists() or (folder / 'output').exists())
        self.assertTrue((folder / 'source.pdf').exists())
        (folder / 'source.pdf').unlink()
        app.JOBS[job_id]['status'] = 'error'
        with patch.object(app, 'engine_command', return_value=['audiveris']), patch.object(app.POOL, 'submit') as submit:
            self.assertEqual(app.retry_job(job_id)[0], 410)
            submit.assert_not_called()

    def test_delete_removes_finished_job_but_not_active_one(self):
        job_id = 'd' * 32
        folder = self.data / job_id
        folder.mkdir()
        (folder / 'source.pdf').write_bytes(b'%PDF-1.4\n')
        app.JOBS[job_id] = {'status': 'running', 'files': []}
        self.assertEqual(app.delete_job(job_id)[0], 409)
        self.assertTrue(folder.exists())
        app.JOBS[job_id]['status'] = 'done'
        self.assertEqual(app.delete_job(job_id)[0], 200)
        self.assertNotIn(job_id, app.JOBS)
        self.assertFalse(folder.exists())
        self.assertEqual(app.delete_job(job_id)[0], 404)

    def test_progress_is_estimated_from_audiveris_log(self):
        self.assertEqual(app.log_progress('Picked up JAVA_TOOL_OPTIONS')['step'], 'Démarrage du moteur')
        log = 'INFO  [source]   Book 560  | 4 sheets in /x/source.pdf\n'
        log += 'INFO  [source#1]   StepMonitoring 98   | LOAD\nINFO  [source#1]   StepMonitoring 98   | PAGE\n'
        log += 'INFO  [source#2]   StepMonitoring 98   | TEXTS\n'
        progress = app.log_progress(log)
        self.assertEqual((progress['sheet'], progress['sheets'], progress['step']), (2, 4, 'Lecture des paroles'))
        self.assertEqual(progress['percent'], round(95 * (1 + 12 / 20) / 4))
        self.assertEqual(app.log_progress(log + 'INFO  [source]  PartwiseBuilder 2707 | Exporting sheet(s): [#1]\n')['percent'], 97)

    def test_job_list_reports_progress_and_queue_position(self):
        running, queued = 'e' * 32, 'f' * 32
        (self.data / running).mkdir()
        (self.data / running / 'conversion.log').write_text('INFO [source] Book 1 | 1 sheet in x\nINFO  [source#1]  StepMonitoring 98   | TEXTS\n')
        app.JOBS[running] = {'status': 'running', 'files': [], 'started': app.time.time() - 120}
        app.JOBS[queued] = {'status': 'queued', 'files': []}
        views = {v['id']: v for v in app.job_views(list(app.JOBS.items()))}
        progress = views[running]['progress']
        self.assertEqual(progress['percent'], 57)
        self.assertGreaterEqual(progress['elapsed'], 120)
        self.assertIn('remaining', progress)
        self.assertEqual(views[queued]['ahead'], 1)
        self.assertNotIn('progress', app.JOBS[running])

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
                self.assertIn(b'Vos partitions', response.read())
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
