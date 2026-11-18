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
        # Lax so that the link from Paheko keeps the session (see shared_server.login)
        for flag in ['Secure','HttpOnly','SameSite=Lax','Path=/']:
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

    def test_logout_ends_the_session_and_clears_the_cookie(self):
        import http.client
        data = json.dumps({'username':'alice','password':'test-password-123'}).encode()
        with self.request('/login', data=data, headers={'Content-Type':'application/json'}) as r:
            cookie = r.headers['Set-Cookie'].split(';')[0]
        with self.request('/api/me', headers={'Cookie':cookie}) as r:
            self.assertEqual(r.status, 200)

        def logout(headers):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            connection.request('POST', '/deconnexion', b'', headers)
            response = connection.getresponse()
            response.read()
            connection.close()
            return response
        refused = logout({'Cookie':cookie, 'Origin':'https://attacker.example'})
        self.assertEqual(refused.status, 403)
        with self.request('/api/me', headers={'Cookie':cookie}) as r:  # a foreign page cannot log you out
            self.assertEqual(r.status, 200)
        done = logout({'Cookie':cookie})
        self.assertEqual((done.status, done.getheader('Location')), (303, '/'))
        self.assertIn('Max-Age=0', done.getheader('Set-Cookie'))
        self.assertTrue(done.getheader('Set-Cookie').startswith('__Host-omr_session=;'))
        # the old cookie no longer opens anything, even if the browser kept it
        with self.assertRaises(HTTPError) as e:
            self.request('/api/jobs', headers={'Cookie':cookie})
        self.assertEqual(e.exception.code, 401)
        e.exception.close()
        # without a session (already logged out, expired), logging out still answers
        self.assertEqual(logout({}).status, 303)
        with self.request('/session.js', 'alice') as r:
            self.assertIn(b'/deconnexion', r.read())

    def test_admin_dashboard_is_restricted_and_minimal(self):
        config=self.root/'administrators.json';config.write_text('["alice"]')
        with patch.object(shared,'ADMINS',config):
            for path in ['/admin','/api/admin']:
                with self.assertRaises(HTTPError) as e:self.request(path,'bob')
                self.assertEqual(e.exception.code,403);e.exception.close()
            with self.request('/api/admin','alice') as r:
                dashboard=json.load(r)
                self.assertEqual(dashboard['counts']['done'],1)
                self.assertEqual([(j['id'],j['owner']) for j in dashboard['recent']],[(self.job,'alice')])
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

    def test_only_owner_can_retry_or_delete(self):
        folder = app.DATA / self.job
        (folder / 'source.pdf').write_bytes(b'%PDF-1.4\n')
        app.JOBS[self.job].update(status='error')
        with patch.object(app, 'engine_command', return_value=['audiveris']), patch.object(app.POOL, 'submit') as submit:
            for operation in ('retry', 'delete'):
                with self.assertRaises(HTTPError) as e:self.request(f'/api/jobs/{self.job}/{operation}','bob',data=b'')
                self.assertEqual(e.exception.code,404);e.exception.close()
                with self.assertRaises(HTTPError) as e:self.request(f'/api/jobs/{self.job}/{operation}','alice',data=b'',headers={'Origin':'https://evil.example'})
                self.assertEqual(e.exception.code,403);e.exception.close()
            submit.assert_not_called()
            self.assertTrue(folder.exists())
            with self.request(f'/api/jobs/{self.job}/retry','alice',data=b'') as r:self.assertEqual(json.load(r)['status'],'queued')
            self.assertEqual(app.JOBS[self.job]['owner'],'alice')
            app.JOBS[self.job]['status']='error'
            with self.request(f'/api/jobs/{self.job}/delete','alice',data=b'') as r:self.assertEqual(json.load(r)['deleted'],self.job)
        self.assertFalse(folder.exists())

    def test_forgot_password_is_public_generic_and_rate_limited(self):
        with self.request('/mot-de-passe-oublie') as r:self.assertIn('identifiant ou votre adresse e-mail', r.read().decode())
        with self.assertRaises(HTTPError) as e:self.request('/')
        self.assertIn('/mot-de-passe-oublie', e.exception.read().decode());e.exception.close()
        shared.SharedHandler.reset_requests.clear()
        with patch.object(shared, 'send_reset') as send:
            answers = []
            for identifier in ('alice', 'inconnu', 'alice'):
                with self.request('/mot-de-passe-oublie', data=f'identifier={identifier}'.encode()) as r:answers.append(r.read())
            self.assertEqual(answers[0], answers[1])  # same answer whether the account exists or not
            for _ in range(50):
                if send.call_count >= 2:break
                time.sleep(0.05)
            self.assertEqual(sorted(c.args[0] for c in send.call_args_list), ['alice', 'inconnu'])  # second "alice" within 15 min is ignored
            with self.assertRaises(HTTPError) as e:self.request('/mot-de-passe-oublie', data=b'identifier=alice', headers={'Origin':'https://evil.example'})
            self.assertEqual(e.exception.code,403);e.exception.close()
            for _ in range(10):
                try:self.request('/mot-de-passe-oublie', data=b'identifier=x').close()
                except HTTPError as error:error.close()
            with self.assertRaises(HTTPError) as e:self.request('/mot-de-passe-oublie', data=b'identifier=y')
            self.assertEqual(e.exception.code,429);e.exception.close()
        shared.SharedHandler.reset_requests.clear()

    def test_native_form_login_redirects_with_session(self):
        import http.client
        def post(body):
            c = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
            c.request('POST', '/login', body, {'Content-Type': 'application/x-www-form-urlencoded'})
            r = c.getresponse(); r.read(); c.close(); return r
        r = post('username=alice&password=test-password-123&website=')
        self.assertEqual((r.status, r.getheader('Location')), (303, '/'))
        cookie = r.getheader('Set-Cookie').split(';')[0]
        with self.request('/', headers={'Cookie': cookie}) as page:self.assertEqual(page.status, 200)
        for body in ('username=alice&password=wrong-password-1', 'username=alice&password=test-password-123&website=spam'):
            r = post(body)
            self.assertEqual((r.status, r.getheader('Location'), r.getheader('Set-Cookie')), (303, '/?erreur=1', None))
        with self.assertRaises(HTTPError) as e:self.request('/')
        self.assertIn(b'method="post" action="/login"', e.exception.read());e.exception.close()

    def test_robots_are_kept_out(self):
        with self.request('/robots.txt') as r:
            self.assertIn(b'Disallow: /', r.read())
            self.assertIn('noindex', r.headers['X-Robots-Tag'])
        with self.assertRaises(HTTPError) as e:self.request('/')
        self.assertIn('noindex', e.exception.headers['X-Robots-Tag'])
        self.assertIn(b'name="website"', e.exception.read());e.exception.close()
        # Correct password, but the hidden field is filled: refused like a wrong password.
        body = json.dumps({'username':'alice','password':'test-password-123','website':'https://spam.example'}).encode()
        with self.assertRaises(HTTPError) as e:self.request('/login', data=body, headers={'Content-Type':'application/json'})
        self.assertEqual(e.exception.code,401);self.assertNotIn('Set-Cookie', e.exception.headers);e.exception.close()
        body = json.dumps({'username':'alice','password':'test-password-123','website':''}).encode()
        with self.request('/login', data=body, headers={'Content-Type':'application/json'}) as r:self.assertIn('Set-Cookie', r.headers)
        shared.SharedHandler.reset_requests.clear()
        with patch.object(shared, 'send_reset') as send:
            with self.request('/mot-de-passe-oublie', data=b'identifier=alice&website=spam') as r:self.assertIn('Si ce compte existe', r.read().decode())
            time.sleep(0.2)
            send.assert_not_called()

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
