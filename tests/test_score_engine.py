from pathlib import Path
import subprocess
import unittest


class ScoreEngineTests(unittest.TestCase):
    def test_audio_timeline_and_cursor(self):
        result=subprocess.run(['node',str(Path(__file__).with_suffix('.js'))],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_paheko_and_converter_share_exact_parser(self):
        project=Path(__file__).resolve().parents[2]
        if not (project/'modules/chorale/score-engine.js').exists():
            self.skipTest('Le module Paheko est vérifié dans le projet complet.')
        self.assertEqual((project/'convertisseur/static/score-engine.js').read_bytes(),(project/'modules/chorale/score-engine.js').read_bytes())


if __name__=='__main__':unittest.main()
