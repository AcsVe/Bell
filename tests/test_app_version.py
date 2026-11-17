"""اختبار رقم نسخة النظام الذي تعتمد عليه تنبيه التحديث: يتغيّر بتغيّر آخر إيداع ولا يكشفه."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flask import Flask, render_template_string  # noqa: E402

import app_version as av  # noqa: E402

SHA1 = 'a' * 40
SHA2 = 'b' * 40


def _repo(sha):
    base = tempfile.mkdtemp()
    os.makedirs(os.path.join(base, '.git', 'refs', 'heads'))
    with open(os.path.join(base, '.git', 'HEAD'), 'w') as f:
        f.write('ref: refs/heads/main\n')
    with open(os.path.join(base, '.git', 'refs', 'heads', 'main'), 'w') as f:
        f.write(sha + '\n')
    return base


def _client(base):
    app = Flask(__name__)
    av.install(app, base)
    app.add_url_rule('/p', 'p', lambda: render_template_string('{{ app_version }}'))
    return app.test_client()


class AppVersionTest(unittest.TestCase):
    def test_endpoint_and_page_agree(self):
        c = _client(_repo(SHA1))
        v = c.get('/api/app-version').get_json()['version']
        self.assertEqual(len(v), 12)
        self.assertEqual(c.get('/p').get_data(as_text=True), v)
        self.assertNotIn(SHA1[:12], v)
        self.assertEqual(c.get('/api/app-version').headers['Cache-Control'], 'no-store')

    def test_changes_with_the_commit(self):
        a = _client(_repo(SHA1)).get('/api/app-version').get_json()['version']
        b = _client(_repo(SHA2)).get('/api/app-version').get_json()['version']
        self.assertNotEqual(a, b)

    def test_works_without_git(self):
        base = tempfile.mkdtemp()
        with open(os.path.join(base, 'x.py'), 'w') as f:
            f.write('1')
        v = _client(base).get('/api/app-version').get_json()['version']
        self.assertEqual(len(v), 12)


if __name__ == '__main__':
    unittest.main()
