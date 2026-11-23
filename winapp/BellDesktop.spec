# -*- mode: python -*-
# بناء نسخة سطح المكتب (مجلد واحد): pyinstaller winapp/BellDesktop.spec --noconfirm
# يُنتج برنامجين في المجلد نفسه: BellDesktop.exe (بلا نافذة أوامر، للتشغيل مع ويندوز) وBellDesktopTools.exe (بنافذة أوامر
# للأوامر المساعدة والفحص الذاتي). القوالب والملفات الثابتة تُنسخ كما هي؛ الكود يُضمَّن مترجَماً (pyc).
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

datas = [(os.path.join(ROOT, 'templates'), 'templates'), (os.path.join(ROOT, 'static'), 'static')]
binaries = []
hidden = ['waitress', 'bell_player', 'common', 'certifi', 'configparser', 'queue', 'zoneinfo']
for pkg in ('tzdata', 'certifi', 'openpyxl', 'et_xmlfile', 'waitress'):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass
# كل وحدات النظام في جذر المستودع (بعضها يُستورد داخل الدوال)، وحزمة blueprints
skip = {'serve'}
hidden += [f[:-3] for f in os.listdir(ROOT) if f.endswith('.py') and f[:-3] not in skip]
hidden += ['blueprints.' + f[:-3] for f in os.listdir(os.path.join(ROOT, 'blueprints')) if f.endswith('.py') and f != '__init__.py']

# مجلد bell_player أولاً في المسار حتى تُقرأ الوحدة bell_player.py لا المجلد بوصفه حزمة
a = Analysis([os.path.join(ROOT, 'winapp', 'bell_desktop.py')],
             pathex=[os.path.join(ROOT, 'bell_player'), ROOT, os.path.join(ROOT, 'ops')],
             binaries=binaries,
             datas=datas,
             hiddenimports=hidden,
             excludes=['tkinter', 'pytest', 'playwright', 'matplotlib', 'IPython', 'gunicorn', 'psycopg'],
             noarchive=False)
pyz = PYZ(a.pure)
icon = os.path.join(ROOT, 'bell_player', 'bell.ico')
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BellDesktop', icon=icon, console=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BellDesktopTools', icon=icon, console=True, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name='BellDesktop', upx=False)
