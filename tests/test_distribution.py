import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'distribution'))
import build  # noqa: E402


class ArchiveTests(unittest.TestCase):
    def test_archive_holds_the_local_application_only(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(build, 'DIST', Path(temp)):
            path = build.build_linux()
            with tarfile.open(path) as archive:
                members = {m.name: m for m in archive.getmembers()}
        names = set(members)
        for name in ('installer.sh', 'LISEZMOI.txt', 'programme/app.py', 'programme/lanceur.py', 'programme/paroles.py',
                     'programme/static/paroles.html', 'programme/static/icone.svg', 'programme/desinstaller.sh', 'programme/VERSION'):
            self.assertIn('atelier-partitions/' + name, names)
        for forbidden in ('private', 'data/', 'tests/', 'shared_server', 'sync_membres', '.venv', '__pycache__', 'accounts'):
            self.assertFalse([n for n in names if forbidden in n], forbidden)
        self.assertEqual(members['atelier-partitions/installer.sh'].mode, 0o755)
        self.assertEqual(members['atelier-partitions/programme/app.py'].mode, 0o644)

    def test_installer_pins_audiveris_with_checksums(self):
        script = (ROOT / 'distribution' / 'linux' / 'installer.sh').read_text(encoding='utf-8')
        version = re.search(r'^AUDIVERIS_VERSION=(\S+)$', script, re.M)[1]
        for release in ('2204', '2404'):
            name = re.search(rf'^AUDIVERIS_{release}=(\S+)$', script, re.M)[1]
            self.assertIn(version, name)
            self.assertRegex(re.search(rf'^AUDIVERIS_{release}_SHA256=(\S+)$', script, re.M)[1], r'^[0-9a-f]{64}$')
        self.assertIn('sha256sum -c', script)
        for path in (ROOT / 'distribution' / 'linux').glob('*.sh'):
            self.assertEqual(subprocess.run(['bash', '-n', str(path)], check=False).returncode, 0, path)

    def test_windows_archive_is_ready_for_powershell(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(build, 'DIST', Path(temp)):
            with zipfile.ZipFile(build.build_windows()) as archive:
                names = set(archive.namelist())
                files = {name: archive.read(name) for name in names if name.endswith(('.ps1', '.cmd', 'LISEZMOI.txt'))}
        for name in ('installer.cmd', 'installer.ps1', 'LISEZMOI.txt', 'programme/app.py', 'programme/static/icone.ico',
                     'programme/desinstaller.ps1', 'programme/desinstaller.cmd', 'programme/VERSION'):
            self.assertIn('atelier-partitions/' + name, names)
        for forbidden in ('private', 'data/', 'tests/', 'shared_server', 'sync_membres', '.sh'):
            self.assertFalse([n for n in names if forbidden in n], forbidden)
        for name, data in files.items():
            self.assertNotIn(b'\n', data.replace(b'\r\n', b''), name)  # Windows line endings only
            if name.endswith('.ps1'):
                self.assertTrue(data.startswith(b'\xef\xbb\xbf'), name)  # UTF-8 BOM for PowerShell 5.1
            if name.endswith('.cmd'):
                self.assertTrue(data.isascii(), name)

    def test_both_installers_pin_the_same_downloads(self):
        linux = (ROOT / 'distribution' / 'linux' / 'installer.sh').read_text(encoding='utf-8')
        windows = (ROOT / 'distribution' / 'windows' / 'installer.ps1').read_text(encoding='utf-8')
        for language in ('eng', 'fra'):
            digest = re.search(rf'^TESSDATA_SHA256_{language}=(\S+)$', linux, re.M)[1]
            self.assertRegex(windows, rf"{language} = '{digest}'")
        self.assertIn(re.search(r'^AUDIVERIS_VERSION=(\S+)$', linux, re.M)[1], re.search(r"^\$AudiverisVersion = '(\S+)'", windows, re.M)[1])
        self.assertRegex(re.search(r"^\$AudiverisSha256 = '(\S+)'", windows, re.M)[1], r'^[0-9a-f]{64}$')
        self.assertIn('Get-FileHash', windows)
        self.assertIn('4.1.0', linux)
        self.assertIn('4.1.0', windows)


class LauncherTests(unittest.TestCase):
    def test_starts_once_then_stops(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            environment = dict(os.environ, PARTITIONS_HOME=temp, ATELIER_PORT=str(port))

            def launcher(*arguments):
                return subprocess.run([sys.executable, str(ROOT / 'lanceur.py'), *arguments], env=environment,
                                      capture_output=True, text=True, timeout=60)
            try:
                self.assertEqual(launcher('--sans-navigateur').returncode, 0)
                pid = (Path(temp) / 'atelier.pid').read_text(encoding='utf-8')
                self.assertEqual(launcher('--sans-navigateur').returncode, 0)  # already running: not started twice
                self.assertEqual((Path(temp) / 'atelier.pid').read_text(encoding='utf-8'), pid)
                self.assertTrue((Path(temp) / 'data').is_dir())
            finally:
                self.assertIn('arrêtée', launcher('--arreter').stdout)
            self.assertIn('ne tournait pas', launcher('--arreter').stdout)
