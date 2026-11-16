import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
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
