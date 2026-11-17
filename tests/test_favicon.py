"""اختبار أيقونة التبويب الاختيارية: تحلّ محل الافتراضية عند ضبط FAVICON_FILE، ولا أثر لها بدونه."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, render_template_string  # noqa: E402

import brand_icon  # noqa: E402

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 16


class FaviconTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        brand_icon.install(self.app)
        self.app.add_url_rule('/p', 'p', lambda: render_template_string('{{ favicon_url }}'))
        self.old = os.environ.pop('FAVICON_FILE', None)

    def tearDown(self):
        os.environ.pop('FAVICON_FILE', None)
        if self.old is not None:
            os.environ['FAVICON_FILE'] = self.old

    def test_off_by_default(self):
        c = self.app.test_client()
        self.assertEqual(c.get('/p').get_data(as_text=True), '')
        self.assertEqual(c.get('/brand-favicon.png').status_code, 404)

    def test_missing_file_is_ignored(self):
        os.environ['FAVICON_FILE'] = '/nonexistent/icon.png'
        c = self.app.test_client()
        self.assertEqual(c.get('/p').get_data(as_text=True), '')
        self.assertEqual(c.get('/brand-favicon.png').status_code, 404)

    def test_configured_file_is_served(self):
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(PNG)
        try:
            os.environ['FAVICON_FILE'] = f.name
            c = self.app.test_client()
            self.assertTrue(c.get('/p').get_data(as_text=True).startswith('/brand-favicon.png?v='))
            r = c.get('/brand-favicon.png')
            self.assertEqual((r.status_code, r.mimetype, r.data), (200, 'image/png', PNG))
        finally:
            os.remove(f.name)


if __name__ == '__main__':
    unittest.main()
