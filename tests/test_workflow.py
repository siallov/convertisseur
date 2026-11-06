import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlparse

import app


class WorkflowTests(unittest.TestCase):
    def test_classification_and_safe_filename(self):
        details = app.score_metadata({'X-Score-Title':quote('Ave / verum'), 'X-Score-Composer':quote('Mozart'), 'X-Score-Part':quote('Ténor'), 'X-Score-Song':'12'})
        self.assertEqual(details['part'],'Ténor')
        self.assertEqual(app.score_metadata({'X-Score-Part':'Baryton'})['part'],'Baryton')
        name=app.download_name(dict(details,name='scan.pdf'),'partition-1.musicxml')
        self.assertEqual(name,'Ave _ verum — Mozart — Ténor.musicxml')
        with self.assertRaises(ValueError):app.score_metadata({'X-Score-Part':'inconnu'})
        with self.assertRaises(ValueError):app.score_metadata({'X-Score-Song':'../12'})

    def test_specific_error_actions(self):
        for log,code in [('Too large image','page_too_large'),('OutOfMemoryError: Java heap space','memory'),('encrypted password','protected_pdf'),('Error loading invalid PDF','unreadable_pdf'),('Too low interline','recognition')]:
            with self.subTest(code=code):
                diagnostic=app.diagnose(log)
                self.assertEqual(diagnostic[0],code)
                self.assertTrue(diagnostic[2])

    def run_conversion(self,publication):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);job='c'*32;folder=root/job;folder.mkdir()
        (folder/'source.pdf').write_bytes(b'%PDF-1.4\n')
        data={'name':'scan.pdf','title':'Ave verum','composer':'Mozart','part':'Alto','song_id':'12','status':'queued','files':[]}
        def engine(*args,**kwargs):
            output=folder/'output';output.mkdir()
            with zipfile.ZipFile(output/'score.mxl','w') as z:
                z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                z.writestr('score.xml','<score-partwise version="4.0"><part-list/></score-partwise>')
            return SimpleNamespace(returncode=0)
        app.JOBS[job]=data
        with patch.object(app,'DATA',root), patch.object(app,'pdf_resolution_options',return_value=[]), patch.object(app.subprocess,'run',side_effect=engine), patch.object(app.paheko_upload,'publish',return_value=publication) as publish, patch.object(app.paheko_upload,'load_config',return_value={'url':'https://chorale-bem.paheko.cloud/dav/documents/'}):
            app.convert(job,['audiveris'])
            self.assertEqual(app.JOBS[job]['status'], 'review')
            self.assertEqual(app.JOBS[job]['upload'], 'pending')
            publish.assert_not_called()
            app.publish_job(job)
            result=dict(app.JOBS[job]);xml=ET.parse(folder/'partition-1.musicxml').getroot()
            self.assertEqual(xml.findtext('work/work-title'),'Ave verum')
            self.assertEqual(xml.findtext('identification/creator'),'Mozart')
            self.assertIn('Ave verum — Mozart — Alto.pdf',[n for n,p in publish.call_args.args[0]])
            self.assertEqual(app.job_details(folder)['upload'],publication['state'])
        app.JOBS.pop(job,None)
        return result

    def test_success_links_exact_new_score_and_song(self):
        result=self.run_conversion({'state':'sent','message':'Envoyé dans Paheko Documents.','sent':['Ave verum — Mozart — Alto.musicxml']})
        link=result['reader_links'][0]['url']
        self.assertEqual(parse_qs(urlparse(link).query)['score'],['Ave verum — Mozart — Alto'])
        self.assertEqual(parse_qs(urlparse(result['song_url']).query)['song'],['12'])

    def test_conflicts_never_open_an_old_score(self):
        result=self.run_conversion({'state':'existing','message':'Déjà présent.','sent':[]})
        self.assertEqual(result['reader_links'],[])
        self.assertTrue(result['action'])

    def test_upload_failure_preserves_downloads(self):
        result=self.run_conversion({'state':'failed','message':'Envoi impossible.','sent':[]})
        self.assertEqual(result['status'],'done')
        self.assertEqual(result['error_code'],'upload_failed')
        self.assertEqual(len(result['files']),2)
        self.assertTrue(result['action'])
