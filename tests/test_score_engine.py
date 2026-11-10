import subprocess
import unittest
from pathlib import Path


class ScoreEngineTests(unittest.TestCase):
    def test_audio_timeline_and_cursor(self):
        result=subprocess.run(['node',str(Path(__file__).with_suffix('.js'))],check=False,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_paheko_and_converter_share_exact_parser(self):
        project=Path(__file__).resolve().parents[2]
        if not (project/'modules/chorale/score-engine.js').exists():
            self.skipTest('Le module Paheko est vérifié dans le projet complet.')
        self.assertEqual((project/'convertisseur/static/score-engine.js').read_bytes(),(project/'modules/chorale/score-engine.js').read_bytes())


if __name__=='__main__':unittest.main()


class ChoirSamplesTests(unittest.TestCase):
    def test_choir_samples_cover_every_semitone_and_are_served(self):
        import base64
        import json
        from pathlib import Path

        import app
        names = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
        for filename in ('choir_aahs.json', 'voice_oohs.json', 'piano.json'):
            path = Path(app.ROOT) / 'static' / 'vendor' / filename
            samples = json.loads(path.read_text())
            for midi in range(36, 85):
                key = f'{names[midi % 12]}{midi // 12 - 1}'
                self.assertIn(key, samples)
                self.assertTrue(base64.b64decode(samples[key]))
        self.assertTrue((path.parent / 'CHOIR_LICENSE.txt').is_file())
