import json
import tempfile
import threading
import unittest
from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app
import envoi_liseuse

PDF = b'%PDF-1.7\n% partition\n'
SETTINGS = {'host': 'smtp.exemple.fr', 'port': 465, 'user': 'u', 'password': 'p', 'from': 'chorale@exemple.fr'}


class SendToReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.smtp_file = Path(self.temp.name) / 'smtp.json'
        self.patches = [patch.object(envoi_liseuse, 'SMTP', self.smtp_file), patch.object(envoi_liseuse, '_SENT', {})]
        for p in self.patches:
            p.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def configure(self):
        self.smtp_file.write_text(json.dumps(SETTINGS))

    def post(self, body=PDF, recipient='marie.durand@pbsync.com', name='chant_liseuse.pdf'):
        request = Request(self.base + '/api/liseuse/envoi', data=body,
                          headers={'Content-Type': 'application/pdf', 'X-Filename': name, 'X-Recipient': recipient})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_status_gives_the_sender_only_when_configured(self):
        with urlopen(self.base + '/api/liseuse/statut') as response:
            self.assertEqual(json.loads(response.read())['sender'], '')
        self.configure()
        with urlopen(self.base + '/api/liseuse/statut') as response:
            self.assertEqual(json.loads(response.read())['sender'], 'chorale@exemple.fr')

    def test_sends_the_pdf_as_attachment(self):
        self.configure()
        with patch.object(envoi_liseuse.smtplib, 'SMTP_SSL') as smtp:
            code, result = self.post(name='le%20duc_liseuse.pdf')
        self.assertEqual((code, result), (200, {'sent': 'marie.durand@pbsync.com'}))
        smtp.assert_called_once_with('smtp.exemple.fr', 465, timeout=60)
        server = smtp.return_value.__enter__.return_value
        server.login.assert_called_once_with('u', 'p')
        message = message_from_bytes(server.send_message.call_args[0][0].as_bytes())
        self.assertEqual(message['To'], 'marie.durand@pbsync.com')
        self.assertEqual(message['From'], 'chorale@exemple.fr')
        attachment = [part for part in message.walk() if part.get_filename()][0]
        self.assertEqual(attachment.get_filename(), 'le duc_liseuse.pdf')
        self.assertEqual(attachment.get_payload(decode=True), PDF)

    def test_refuses_other_addresses_and_files(self):
        self.configure()
        with patch.object(envoi_liseuse.smtplib, 'SMTP_SSL') as smtp:
            for recipient in ('quelqu.un@gmail.com', 'x@pbsync.com.exemple.fr', 'a@pbsync.com%0ABcc:b@x.fr', ''):
                self.assertEqual(self.post(recipient=recipient)[0], 400, recipient)
            self.assertEqual(self.post(body=b'pas un pdf')[0], 400)
        smtp.assert_not_called()

    def test_without_mail_account_or_when_smtp_fails(self):
        self.assertEqual(self.post()[0], 503)
        self.configure()
        with patch.object(envoi_liseuse.smtplib, 'SMTP_SSL', side_effect=OSError('réseau')):
            code, result = self.post()
        self.assertEqual(code, 503)
        self.assertIn('n’a pas pu partir', result['error'])

    def test_limits_the_number_of_sends(self):
        self.configure()
        with patch.object(envoi_liseuse.smtplib, 'SMTP_SSL'), patch.object(envoi_liseuse, 'PER_HOUR', 2):
            self.assertEqual([self.post()[0] for _ in range(3)], [200, 200, 400])

    def test_refuses_foreign_origin(self):
        self.configure()
        request = Request(self.base + '/api/liseuse/envoi', data=PDF,
                          headers={'Origin': 'https://ailleurs.exemple', 'X-Recipient': 'a@pbsync.com'})
        with patch.object(envoi_liseuse.smtplib, 'SMTP_SSL') as smtp, self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        smtp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
