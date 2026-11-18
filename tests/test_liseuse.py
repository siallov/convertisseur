import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app

try:
    import pymupdf
    import liseuse
except ImportError:
    liseuse = None

SYSTEMS_PER_PAGE = (3, 4, 4)


def choir_score():
    """Three A4 pages of three-staff systems: lyrics under each staff, a line joining the
    staves of a system on the left, measure bars aligned across systems, a page number."""
    doc = pymupdf.open()
    for number, count in enumerate(SYSTEMS_PER_PAGE):
        page = doc.new_page(width=595, height=842)
        if number == 0:
            page.insert_text((200, 70), 'Titre du chant', fontsize=20, fontname='tiro')
        y = 130 if number == 0 else 60
        for _ in range(count):
            for staff in range(3):
                top = y + staff * 52
                for line in range(5):
                    page.draw_line((50, top + line * 7), (545, top + line * 7), width=0.6)
                page.insert_text((70, top + 48), 'la li lo', fontsize=9)
            for x in (50, 170, 290, 410, 545):
                page.draw_line((x, y), (x, y + 2 * 52 + 28), width=0.8)
            y += 3 * 52 + 40
        page.insert_text((290, 825), str(number + 1), fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def scanned(data):
    src = pymupdf.open(stream=data, filetype='pdf')
    out = pymupdf.open()
    for page in src:
        image = out.new_page(width=page.rect.width, height=page.rect.height)
        image.insert_image(image.rect, pixmap=page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY))
    return out.tobytes()


@unittest.skipUnless(liseuse is not None, 'PyMuPDF ou numpy non installé')
class ReaderLayoutTests(unittest.TestCase):
    def check(self, data):
        body, info = liseuse.convert(data)
        if liseuse._find_staves is not None:
            self.assertEqual(info['systems'], sum(SYSTEMS_PER_PAGE))
        with pymupdf.open(stream=body, filetype='pdf') as doc:
            self.assertEqual(info['pages'], len(doc))
            for page in doc:
                self.assertAlmostEqual(page.rect.width, 336.96, places=1)
                self.assertAlmostEqual(page.rect.height, 449.28, places=1)
        return body

    def test_digital_score_keeps_text_and_drops_page_numbers(self):
        body = self.check(choir_score())
        with pymupdf.open(stream=body, filetype='pdf') as doc:
            text = ''.join(page.get_text() for page in doc)
        self.assertIn('Titre du chant', text)
        self.assertEqual(text.count('la li lo'), 3 * sum(SYSTEMS_PER_PAGE))
        for number in ('1', '2', '3'):
            self.assertNotIn(f'\n{number}\n', f'\n{text}')

    def test_scanned_score(self):
        self.check(scanned(choir_score()))

    def test_system_is_never_split(self):
        blocks = []
        with pymupdf.open(stream=choir_score(), filetype='pdf') as src:
            for number, page in enumerate(src):
                blocks += liseuse.page_blocks(page, number)
        systems = [b for b in blocks if b.system]
        if liseuse._find_staves is not None:
            # three staves and their lyrics: about 132 pt of music plus the last line of lyrics
            for block in systems:
                self.assertGreater(block.clip.height, 140)
        for page in liseuse.layout(blocks):
            for _, target in page:
                self.assertLessEqual(target.y1, liseuse.PAGE_H - liseuse.MARGIN + 0.01)

    def test_rejects_empty_document(self):
        doc = pymupdf.open()
        doc.new_page()
        with self.assertRaises(ValueError):
            liseuse.convert(doc.tobytes())


@unittest.skipUnless(liseuse is not None, 'PyMuPDF ou numpy non installé')
class ReaderRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(app, 'DATA', Path(self.temp.name))
        self.patch.start()
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.patch.stop()
        self.temp.cleanup()

    def test_converts_and_names_the_download(self):
        request = Request(self.base + '/api/liseuse', data=choir_score(),
                          headers={'Content-Type': 'application/pdf', 'X-Filename': 'chant%20test.pdf'})
        with urlopen(request) as response:
            self.assertEqual(response.headers['Content-Type'], 'application/pdf')
            self.assertIn('chant test_liseuse.pdf', response.headers['Content-Disposition'])
            self.assertGreater(int(response.headers['X-Pages']), 0)
            self.assertTrue(response.read().startswith(b'%PDF-'))
        with urlopen(self.base + '/liseuse') as response:
            self.assertIn('Format liseuse', response.read().decode())

    def test_refuses_what_is_not_a_pdf(self):
        request = Request(self.base + '/api/liseuse', data=b'pas un pdf', headers={'Content-Type': 'application/pdf'})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
