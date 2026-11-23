"""الأسماء الإنجليزية للمراحل التي كُتبت بالعربية فقط: تخمين مؤكد عند الإضافة وعند الإقلاع، ولا نستبدل ما كتبه المستخدم."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import create_app  # noqa: E402
from models import Stage, db, ensure_schema, stage_en_guess  # noqa: E402


@pytest.fixture
def env(tmp_path):
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path}/n.db'})
    admin = app.test_client()
    admin.post('/admin/login', data={'username': 'admin', 'password': 'admin'})
    return app, admin


@pytest.mark.parametrize('ar,en', [
    ('المرحلة الأساسية 1-3', 'Primary 1-3'), ('المرحلة الأساسية ٤-٦', 'Primary 4-6'), ('المرحلة الثانوية', 'Secondary'),
    ('المرحلة المتوسطة', 'Middle'), ('رياض الأطفال', 'Kindergarten'), ('الثانوية', 'Secondary'),
    ('المرحلة الأساسية الدنيا', None), ('مدرسة البنات', None), ('', None)])
def test_stage_guess(ar, en):
    assert stage_en_guess(ar) == en


def test_adding_a_stage_fills_the_english_name_unless_typed(env):
    app, admin = env
    admin.post('/admin/stages/add', data={'name_ar': 'المرحلة الأساسية 1-3'})
    admin.post('/admin/stages/add', data={'name_ar': 'المرحلة الثانوية', 'name_en': 'Upper school'})
    admin.post('/admin/stages/add', data={'name_ar': 'مرحلة خاصة'})
    with app.app_context():
        got = {s.name_ar: s.name_en for s in Stage.query.all()}
    assert got == {'المرحلة الأساسية 1-3': 'Primary 1-3', 'المرحلة الثانوية': 'Upper school', 'مرحلة خاصة': None}


def test_startup_backfills_old_stages_without_touching_typed_names(env):
    app, admin = env
    with app.app_context():
        db.session.add_all([Stage(name_ar='المرحلة الثانوية', sort_order=0), Stage(name_ar='المرحلة الأساسية 1-3', name_en='My Primary', sort_order=1)])
        db.session.commit()
        ensure_schema()
        got = {s.name_ar: s.name_en for s in Stage.query.all()}
    assert got == {'المرحلة الثانوية': 'Secondary', 'المرحلة الأساسية 1-3': 'My Primary'}


def test_the_english_interface_shows_the_english_stage_names(env):
    app, admin = env
    admin.post('/admin/stages/add', data={'name_ar': 'المرحلة الأساسية 1-3'})
    with admin.session_transaction() as s:
        s['lang'] = 'en'
    page = admin.get('/admin/').get_data(as_text=True)
    import re
    cards = re.findall(r'<h2[^>]*>(.*?)</h2>', page, re.S)
    assert any('Primary 1-3' in c for c in cards) and not any('المرحلة الأساسية' in c for c in cards)
