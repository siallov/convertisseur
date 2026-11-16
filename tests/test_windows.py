"""Behaviour that differs on Windows, simulated on any system."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import chemins
import paroles


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_existing_installation_keeps_its_folders(self):
        (self.root / 'private').mkdir()
        with patch.object(chemins, 'ROOT', self.root), patch.dict(os.environ, {}, clear=False):
            os.environ.pop('PARTITIONS_HOME', None)
            self.assertEqual(chemins.home(), self.root)

    def test_new_installation_uses_the_user_profile(self):
        environment = {'LOCALAPPDATA': r'C:\Users\Chantal\AppData\Local', 'XDG_DATA_HOME': '/home/chantal/.local/share'}
        with patch.object(chemins, 'ROOT', self.root), patch.dict(os.environ, environment):
            os.environ.pop('PARTITIONS_HOME', None)
            with patch.object(chemins, 'WINDOWS', True):
                self.assertEqual(chemins.home(), Path(r'C:\Users\Chantal\AppData\Local') / 'AtelierPartitions')
            with patch.object(chemins, 'WINDOWS', False):
                self.assertEqual(chemins.home(), Path('/home/chantal/.local/share/atelier-partitions'))
            with patch.dict(os.environ, {'PARTITIONS_HOME': str(self.root / 'ailleurs')}):
                self.assertEqual(chemins.home(), self.root / 'ailleurs')

    def test_program_folders_are_listed_once(self):
        environment = {'ProgramFiles': r'C:\Program Files', 'ProgramW6432': r'C:\Program Files',
                       'ProgramFiles(x86)': r'C:\Program Files (x86)', 'LOCALAPPDATA': r'C:\Users\C\AppData\Local'}
        with patch.dict(os.environ, environment):
            folders = chemins.program_files()
        self.assertEqual(folders, [Path(r'C:\Program Files'), Path(r'C:\Program Files (x86)'),
                                   Path(r'C:\Users\C\AppData\Local') / 'Programs'])


class EngineTests(unittest.TestCase):
    def test_windows_command_keeps_backslashes(self):
        with patch.object(chemins, 'WINDOWS', True):
            self.assertEqual(app.split_command(r'"C:\Program Files\Audiveris\Audiveris.exe" -batch'),
                             [r'C:\Program Files\Audiveris\Audiveris.exe', '-batch'])
        with patch.object(chemins, 'WINDOWS', False):
            self.assertEqual(app.split_command("java -jar '/opt/a b/Audiveris.jar'"), ['java', '-jar', '/opt/a b/Audiveris.jar'])

    def test_audiveris_is_found_in_program_files(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / 'Audiveris' / 'Audiveris.exe'
            executable.parent.mkdir()
            executable.write_bytes(b'')
            with patch.object(chemins, 'WINDOWS', True), patch.object(chemins, 'program_files', return_value=[Path(temp)]), \
                    patch.object(app.shutil, 'which', return_value=None), patch.object(app.os, 'access', return_value=True), \
                    patch.dict(os.environ, {}, clear=False):
                os.environ.pop('AUDIVERIS_CMD', None)
                self.assertEqual(app.engine_command(), [str(executable)])
                with patch.dict(os.environ, {'AUDIVERIS_CMD': f'"{executable}"'}):
                    self.assertEqual(app.engine_command(), [str(executable)])

    def test_tesseract_folder_is_added_to_path(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'Tesseract-OCR').mkdir()
            (Path(temp) / 'Tesseract-OCR' / 'tesseract.exe').write_bytes(b'')
            with patch.object(chemins, 'WINDOWS', True), patch.object(paroles.shutil, 'which', return_value=None), \
                    patch.object(chemins, 'program_files', return_value=[Path(temp)]), patch.dict(os.environ, {'PATH': 'x'}):
                paroles.find_tesseract()
                self.assertTrue(os.environ['PATH'].startswith(str(Path(temp) / 'Tesseract-OCR')))


    def test_audiveris_french_is_used_when_tesseract_lacks_it(self):
        with tempfile.TemporaryDirectory() as temp:
            program = Path(temp) / 'Tesseract-OCR'
            (program / 'tessdata').mkdir(parents=True)
            (program / 'tesseract.exe').write_bytes(b'')
            shared = Path(temp) / 'Roaming' / 'AudiverisLtd' / 'audiveris' / 'tessdata'
            shared.mkdir(parents=True)
            for language in ('eng', 'fra'):
                (shared / f'{language}.traineddata').write_bytes(b'')
            environment = {'APPDATA': str(Path(temp) / 'Roaming'), 'PATH': 'x'}
            with patch.object(chemins, 'WINDOWS', True), patch.object(paroles.shutil, 'which', return_value=str(program / 'tesseract.exe')), \
                    patch.dict(os.environ, environment):
                os.environ.pop('TESSDATA_PREFIX', None)
                paroles.find_tesseract()
                self.assertEqual(os.environ.get('TESSDATA_PREFIX'), str(shared))
                (program / 'tessdata' / 'fra.traineddata').write_bytes(b'')
                os.environ.pop('TESSDATA_PREFIX')
                paroles.find_tesseract()  # Tesseract has French itself: nothing to change
                self.assertNotIn('TESSDATA_PREFIX', os.environ)

class FileTests(unittest.TestCase):
    @unittest.skipUnless(paroles.pymupdf, 'PyMuPDF non installé')
    def test_page_sizes_without_pdfinfo(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'deux pages.pdf'
            doc = pymupdf.open()
            doc.new_page(width=595, height=842)
            doc.new_page(width=2550, height=3507)
            doc.save(path)
            doc.close()
            with patch.object(app.shutil, 'which', return_value=None):
                self.assertEqual(app.page_sizes(path), [(595, 842), (2550, 3507)])
                self.assertEqual(app.pdf_resolution_options(path)[-1], 'org.audiveris.omr.image.ImageLoading.pdfResolution=102')

    def test_results_are_written_in_utf8(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(app, 'DATA', Path(temp)):
            job_id = 'a' * 32
            (Path(temp) / job_id).mkdir()
            app.JOBS[job_id] = {'status': 'running'}
            try:
                app.update(job_id, status='error', message='Échec : clé de sol illisible')
                raw = (Path(temp) / job_id / 'result.json').read_bytes()
            finally:
                app.JOBS.pop(job_id, None)
        self.assertIn('Échec : clé'.encode('utf-8'), raw)
        self.assertEqual(json.loads(raw.decode('utf-8'))['message'], 'Échec : clé de sol illisible')
