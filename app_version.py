"""رقم نسخة النظام: تنبّه به صفحات الإدارة المفتوحة إلى صدور تحديث جديد (static/js/update-notice.js).

الرقم بصمة قصيرة لآخر إيداع في مجلد النظام (git)، وإن لم يوجد مجلد git فبصمة أحدث تاريخ تعديل لملفات النظام.
يُحسب مرة عند تشغيل الخادم، فيتغيّر بعد كل تحديث وإعادة تشغيل، ولا يكشف رقم الإيداع نفسه.
"""
import hashlib
import os

from flask import jsonify

_SKIP = {'.git', '.venv', 'venv', 'data', 'node_modules', '__pycache__', 'backups', 'uploads', 'logs', 'icon-cache'}


def _git_sha(base):
    try:
        git = os.path.join(base, '.git')
        with open(os.path.join(git, 'HEAD'), encoding='utf-8') as f:
            head = f.read().strip()
        if not head.startswith('ref:'):
            return head
        ref = head[4:].strip()
        path = os.path.join(git, *ref.split('/'))
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                return f.read().strip()
        with open(os.path.join(git, 'packed-refs'), encoding='utf-8') as f:
            for line in f:
                if line.strip().endswith(' ' + ref):
                    return line.split()[0]
    except OSError:
        pass
    return ''


def _newest_mtime(base):
    newest = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        for name in files:
            if name.endswith(('.py', '.html', '.js', '.css')):
                try:
                    newest = max(newest, int(os.path.getmtime(os.path.join(root, name))))
                except OSError:
                    pass
    return str(newest)


def compute(base):
    raw = _git_sha(base) or _newest_mtime(base)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def install(app, base):
    version = compute(base)

    @app.route('/api/app-version', endpoint='app_version')
    def app_version():
        resp = jsonify(version=version)
        resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.context_processor
    def _app_version_context():
        return {'app_version': version}
