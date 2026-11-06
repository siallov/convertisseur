import base64
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_pupitres import SCORE

import app
import paheko_upload
import shared_server as shared


class SharedTests(unittest.TestCase):
    def test_pupitre_names_are_owned_and_saved_before_publication(self):
        folder=app.DATA/self.job
        (folder/'partition-1.musicxml').write_bytes(SCORE)
        app.JOBS[self.job]['status']='review'
        endpoint='/api/jobs/'+self.job+'/pupitres'
        with self.request(endpoint+'?file=partition-1.musicxml','alice') as r:
            self.assertEqual(len(json.load(r)['tracks']),2)
        for method in ['GET','POST']:
            with self.assertRaises(HTTPError) as e:
                self.request(endpoint+'?file=partition-1.musicxml','bob',method=method,data=b'{}' if method=='POST' else None)
            self.assertEqual(e.exception.code,404);e.exception.close()
        payload=json.dumps({'file':'partition-1.musicxml','labels':{'P1/1/1':'Alto','P1/1/2':'Baryton'}}).encode()
        with self.request(endpoint,'alice',data=payload,headers={'Content-Type':'application/json'}) as r:
            self.assertEqual([t['label'] for t in json.load(r)['tracks']],['Alto','Baryton'])
        self.assertIn(b'Baryton',(folder/'partition-1.musicxml').read_bytes())
        app.JOBS[self.job]['status']='publishing'
        with self.assertRaises(HTTPError) as e:self.request(endpoint,'alice',data=payload)
        self.assertEqual(e.exception.code,409);e.exception.close()

    def test_pupitre_invalid_input_and_cross_origin_never_change_files(self):
        folder=app.DATA/self.job;(folder/'partition-1.musicxml').write_bytes(SCORE)
        app.JOBS[self.job]['status']='review';endpoint='/api/jobs/'+self.job+'/pupitres'
        for data in [b'[]',b'{',json.dumps({'file':'../source.pdf','labels':{}}).encode(),json.dumps({'file':'partition-1.musicxml','labels':{}}).encode()]:
            with self.assertRaises(HTTPError) as e:self.request(endpoint,'alice',data=data)
            self.assertEqual(e.exception.code,400);e.exception.close()
        with self.assertRaises(HTTPError) as e:self.request(endpoint,'alice',data=b'{}',headers={'Origin':'https://attacker.example'})
        self.assertEqual(e.exception.code,403);e.exception.close()
        self.assertEqual((folder/'partition-1.musicxml').read_bytes(),SCORE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.account_file = self.root / 'accounts.json'
        salt = 'ab' * 16
        digest = hashlib.pbkdf2_hmac('sha256', b'test-password-123', bytes.fromhex(salt), 600000).hex()
        self.account_file.write_text(json.dumps({name: {'salt':salt,'hash':digest} for name in ['alice','bob']}))
        self.dav_file=self.root/'paheko.json'
        self.dav_file.write_text(json.dumps({'url':'https://chorale-bem.paheko.cloud/dav/documents/','folder':'Partitions','user':'bot','password':'fake-secret'}))
        self.patches = [patch.object(paheko_upload, 'CONFIG', self.dav_file),patch.object(shared, 'ACCOUNTS', self.account_file), patch.object(app, 'DATA', self.root / 'jobs')]
        for p in self.patches:p.start()
        app.DATA.mkdir()
        app.JOBS.clear()
        shared.SharedHandler.attempts.clear()
        self.job = 'a'*32
        folder = app.DATA / self.job
        folder.mkdir()
        (folder/'partition-1.musicxml').write_text('<score-partwise/>')
        app.JOBS[self.job] = {'owner':'alice','created':time.time(),'name':'Partition privée','status':'done','files':['partition-1.musicxml']}
        self.server = app.ThreadingHTTPServer(('127.0.0.1',0),shared.SharedHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for p in reversed(self.patches):p.stop()
        app.JOBS.clear()
        self.temp.cleanup()

    def request(self,path,user=None,**options):
        headers=options.pop('headers',{})
        if user:headers['Authorization']='Basic '+base64.b64encode(f'{user}:test-password-123'.encode()).decode()
        return urlopen(Request(self.base+path,headers=headers,**options))

    def test_login_form_has_no_browser_challenge(self):
        with self.assertRaises(HTTPError) as e:
            self.request('/')
        response = e.exception
        self.assertIsNone(response.headers.get('WWW-Authenticate'))
        self.assertIn(b'autocomplete="username"', response.read())
        response.close()

    def test_form_login_cookie_and_expiry(self):
        data = json.dumps({'username':'alice','password':'test-password-123'}).encode()
        with self.request('/login', data=data, headers={'Content-Type':'application/json'}) as r:
            self.assertEqual(json.load(r), {'ok':True})
            cookie = r.headers['Set-Cookie']
        for flag in ['Secure','HttpOnly','SameSite=Strict','Path=/']:
            self.assertIn(flag, cookie)
        cookie = cookie.split(';')[0]
        with self.request('/api/jobs', headers={'Cookie':cookie}) as r:
            self.assertEqual(len(json.load(r)), 1)
        token = cookie.split('=',1)[1]
        shared.SharedHandler.sessions[token] = ('alice', time.time()-1)
        with self.assertRaises(HTTPError) as e:
            self.request('/api/jobs', headers={'Cookie':cookie})
        e.exception.close()
        with self.assertRaises(HTTPError) as e:
            self.request('/login', data=data, headers={'Origin':'https://attacker.example'})
        self.assertEqual(e.exception.code,403)
        e.exception.close()

    def test_admin_dashboard_is_restricted_and_minimal(self):
        config=self.root/'administrators.json';config.write_text('["alice"]')
        with patch.object(shared,'ADMINS',config):
            for path in ['/admin','/api/admin']:
                with self.assertRaises(HTTPError) as e:self.request(path,'bob')
                self.assertEqual(e.exception.code,403);e.exception.close()
            with self.request('/api/admin','alice') as r:
                dashboard=json.load(r)
                self.assertEqual(dashboard['counts']['done'],1)
                self.assertGreater(dashboard['storage_bytes'],0)
                self.assertNotIn('hash',json.dumps(dashboard))
                self.assertNotIn('salt',json.dumps(dashboard))
            with self.request('/api/me','bob') as r:self.assertFalse(json.load(r)['admin'])

    def test_only_owner_can_publish_once_and_origin_is_checked(self):
        app.JOBS[self.job].update(status='review',upload='pending')
        path=f'/api/jobs/{self.job}/publish'
        with patch.object(app.POOL,'submit') as submit:
            with self.assertRaises(HTTPError) as e:self.request(path,'bob',data=b'')
            self.assertEqual(e.exception.code,404);e.exception.close()
            with self.assertRaises(HTTPError) as e:self.request(path,'alice',data=b'',headers={'Origin':'https://evil.example'})
            self.assertEqual(e.exception.code,403);e.exception.close()
            submit.assert_not_called()
            with self.request(path,'alice',data=b'') as r:self.assertEqual(json.load(r)['status'],'publishing')
            with self.request(path,'alice',data=b'') as r:self.assertEqual(json.load(r)['status'],'publishing')
            self.assertEqual(submit.call_count,1)

    def test_settings_are_admin_only_and_never_return_password(self):
        config=self.root/'administrators.json';config.write_text('["alice"]')
        with patch.object(shared,'ADMINS',config):
            with self.assertRaises(HTTPError) as e:self.request('/api/admin/settings','bob')
            self.assertEqual(e.exception.code,403);e.exception.close()
            with self.request('/api/admin/settings','alice') as r:
                settings=json.load(r)
                self.assertTrue(settings['password_saved'])
                self.assertNotIn('fake-secret',json.dumps(settings))
            values={'association':'Autre chorale','converter_url':'https://omr.example.org','webdav_url':'https://autre.example.org/dav/documents/','module_path':'/m/autre_chorale/','folder':'Musique/Partitions','webdav_user':'autre-bot','webdav_password':'new-fake-secret'}
            app.JOBS[self.job]['status']='review'
            with self.assertRaises(HTTPError) as e:self.request('/api/admin/settings','alice',data=json.dumps(values).encode())
            self.assertEqual(e.exception.code,409);e.exception.close()
            app.JOBS[self.job]['status']='done'
            with self.request('/api/admin/settings','alice',data=json.dumps(values).encode()) as r:
                self.assertNotIn('new-fake-secret',json.dumps(json.load(r)))
            with self.request('/api/config','bob') as r:
                public=json.load(r);self.assertEqual(public['paheko_origin'],'https://autre.example.org')
                self.assertNotIn('webdav_user',public)
            with self.request('/api/status',headers={'Origin':'https://autre.example.org'}) as r:
                self.assertEqual(r.headers.get('Access-Control-Allow-Origin'),'https://autre.example.org')
            with self.request('/api/status',headers={'Origin':'https://chorale-bem.paheko.cloud'}) as r:
                self.assertIsNone(r.headers.get('Access-Control-Allow-Origin'))

    def test_public_status_contains_no_user_data(self):
        with patch.object(app, 'engine_command', return_value=['audiveris']), self.request('/api/status',headers={'Origin':'https://chorale-bem.paheko.cloud'}) as r:
            self.assertEqual(json.load(r),{'available':True})
            self.assertEqual(r.headers['Access-Control-Allow-Origin'],'https://chorale-bem.paheko.cloud')
        with self.request('/api/status',headers={'Origin':'https://attacker.example'}) as r:
            self.assertIsNone(r.headers.get('Access-Control-Allow-Origin'))

    def test_anonymous_cannot_read_files_or_interface(self):
        for path in ['/', '/api/jobs',f'/api/jobs/{self.job}/partition-1.musicxml']:
            with self.assertRaises(HTTPError) as e:self.request(path)
            self.assertEqual(e.exception.code,401);e.exception.close()

    def test_users_only_see_their_own_work(self):
        with self.request('/api/jobs','alice') as r:self.assertEqual(len(json.load(r)),1)
        with self.request('/api/jobs','bob') as r:self.assertEqual(json.load(r),[])
        with self.assertRaises(HTTPError) as e:self.request(f'/api/jobs/{self.job}/partition-1.musicxml','bob')
        self.assertEqual(e.exception.code,404);e.exception.close()
        with self.request(f'/api/jobs/{self.job}/partition-1.musicxml','alice') as r:
            self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertEqual(r.read(),b'<score-partwise/>')

    def test_download_is_named_after_the_pdf(self):
        app.JOBS[self.job]['name'] = "003-J'aime le vin.pdf"
        with self.request(f'/api/jobs/{self.job}/partition-1.musicxml','alice') as r:
            disposition = r.headers['Content-Disposition']
        self.assertIn("filename*=UTF-8''003-J%27aime%20le%20vin.musicxml", disposition)

    def test_cross_origin_post_rejected(self):
        with self.assertRaises(HTTPError) as e:self.request('/api/jobs','alice',data=b'%PDF-1.4',headers={'Origin':'https://attacker.example'})
        self.assertEqual(e.exception.code,403);e.exception.close()

    def test_expiration_preserves_active_jobs(self):
        app.JOBS[self.job]['created']=time.time()-shared.RETENTION-1
        active='b'*32
        app.JOBS[active]={'status':'running','created':0}
        shared.cleanup()
        self.assertNotIn(self.job,app.JOBS)
        self.assertFalse((app.DATA/self.job).exists())
        self.assertIn(active,app.JOBS)
