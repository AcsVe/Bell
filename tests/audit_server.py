"""خادم تجربة لفحص المظهر واللغة (tests/ui_audit.py): قاعدة مؤقتة ببيانات عربية أو إنجليزية تُضاف عبر مسارات الإدارة نفسها.

    python tests/audit_server.py <مجلد البيانات> <ar|en> <المنفذ>
يطبع سطر JSON بالمعرّفات ثم يبقى يعمل حتى يُوقَف.
"""
import json
import os
import sys

DATA, LANG, PORT = sys.argv[1], sys.argv[2], int(sys.argv[3])
EN = LANG == 'en'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA, exist_ok=True)
db_path = os.path.join(DATA, 'bell.db')
for ext in ('', '-wal', '-shm'):
    if os.path.exists(db_path + ext):
        os.remove(db_path + ext)
os.environ.update(DATABASE_URL='sqlite:///' + db_path.replace('\\', '/'), DATA_DIR=DATA, SECRET_KEY='audit-secret',
                  ADMIN_USER='admin', ADMIN_PASS='audit-pass-123')
for k in ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL', 'RENDER', 'SECURE_COOKIES'):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import create_app  # noqa: E402
from models import BellProgram, Section, Stage, Teacher  # noqa: E402

L = (lambda ar, en: en if EN else ar)
app = create_app()
c = app.test_client()
c.post('/admin/login', data={'username': 'admin', 'password': 'audit-pass-123'})
c.post('/admin/stages/add', data={'name_ar': L('المرحلة الأساسية 1-3', 'Primary 1-3'), 'name_en': 'Primary 1-3'})
with app.app_context():
    sid = Stage.query.first().id
for kind, dur in [('class', 45), ('class', 45), ('break', 20), ('class', 45)]:
    c.post(f'/admin/stages/{sid}/periods/add', data={'kind': kind, 'duration': dur})
c.post(f'/admin/stages/{sid}/update', data={'name_ar': L('المرحلة الأساسية 1-3', 'Primary 1-3'), 'name_en': 'Primary 1-3',
                                             'active': '1', 'weekdays': ['6', '0', '1', '2', '3']})
c.post(f'/admin/stages/{sid}/grades/add', data={'names': L('الصف الأول', 'Grade 1')})
with app.app_context():
    gid = Stage.query.first().grades[0].id
c.post(f'/admin/grades/{gid}/sections/add', data={'names': L('أ، ب', 'A, B')})
c.post('/admin/teachers/add', data={'stage_id': sid, 'names': L('سارة الخطيب\nمحمد العلي teacher2@school.test',
                                                                 'Sara Khatib\nMohammad Ali teacher2@school.test')})
c.post('/admin/programs/add', data={'name': L('الدوام الشتوي', 'Winter timetable'), 'name_en': 'Winter timetable'})
with app.app_context():
    pid = BellProgram.query.first().id
for kind, dur in [('class', 45), ('break', 15), ('class', 45)]:
    c.post(f'/admin/programs/{pid}/periods/add', data={'kind': kind, 'duration': dur})
with app.app_context():
    sec = Section.query.order_by(Section.id).first()
    t = Teacher.query.order_by(Teacher.id).first()
    for s in Section.query.all():
        c.post(f'/admin/sections/{s.id}/teachers/add', data={'teacher_id': t.id})
    ids = {'stage': sid, 'grade': gid, 'section': sec.id, 'teacher': t.id, 'program': pid, 'code': sec.code}
print(json.dumps(ids), flush=True)

from werkzeug.serving import make_server  # noqa: E402
make_server('127.0.0.1', PORT, app, threaded=True).serve_forever()
