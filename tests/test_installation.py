import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import paheko_upload
import installation_config as config


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'private/paheko.json'
        self.patcher=patch.object(paheko_upload,'CONFIG',self.path);self.patcher.start();self.addCleanup(self.patcher.stop)
        self.values=dict(association='Nouvelle chorale',converter_url='https://omr.example.net',webdav_url='https://chorale.example.net/dav/documents/',module_path='/m/chorale_test/',folder='Musique/Partitions',webdav_user='omr-bot',webdav_password='fake-password')

    def test_generic_default_and_explicit_legacy_compatibility(self):
        self.assertEqual(config.public()['module_path'],'/m/chorale/')
        config.save(dict(self.values,module_path='/m/chorale_bem/'))
        self.assertIn('/m/chorale_bem/?',app.reader_url('Mon chant'))

    def test_new_installation_and_dynamic_links(self):
        result=config.save(self.values)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.assertNotIn('fake-password',json.dumps(result))
        self.assertNotIn('webdav_user',config.public())
        self.assertTrue(app.reader_url('Mon chant').startswith('https://chorale.example.net/m/chorale_test/?'))
        self.assertEqual(paheko_upload.load_config()['folder'],'Musique/Partitions')

    def test_keep_secret_and_require_it_for_new_destination(self):
        config.save(self.values)
        blank=dict(self.values,webdav_password='')
        config.save(blank)
        self.assertEqual(config.raw()['password'],'fake-password')
        with self.assertRaises(ValueError):config.save(dict(blank,webdav_url='https://other.example.net/dav/'))
        self.assertEqual(config.public()['paheko_origin'],'https://chorale.example.net')

    def test_invalid_paths_and_urls_never_save(self):
        for key,value in [('folder','../Partitions'),('folder','A/../B'),('module_path','https://evil.example/m/'),('module_path','/m/../'),('converter_url','http://omr.example.net'),('webdav_url','https://user:secret@chorale.example.net/dav/'),('webdav_url','https://chorale.example.net/dav/?token=x')]:
            with self.subTest(key=key,value=value):
                with self.assertRaises(ValueError):config.save(dict(self.values,**{key:value}))
                self.assertFalse(self.path.exists())
