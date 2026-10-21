"""أدوات مشتركة للخادم المحلي (الكمبيوتر) والهاتف: قراءة .env ومسارات البيانات والنسخ — نظام جرس الحصص."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EN = os.environ.get('IAJ_LANG', os.environ.get('LANG_UI', '')) == 'en'


def t(ar, en):
    return en if EN else ar


def load_env():
    """يقرأ .env (لا يستبدل متغيراً موجوداً مسبقاً). الافتراضي: قاعدة SQLite في مجلد data داخل المشروع
    (خارج الكود — لا يلمسها update.bat)."""
    path = os.path.join(BASE, '.env')
    if os.path.isfile(path):
        with open(path, encoding='utf-8-sig', errors='ignore') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    os.environ.setdefault('DATA_DIR', os.path.join(BASE, 'data'))
    os.makedirs(os.environ['DATA_DIR'], exist_ok=True)
    os.environ.setdefault('BELL_DB_PATH', os.path.join(os.environ['DATA_DIR'], 'bell.db'))
    # التطبيق يقرأ DATABASE_URL — على الكمبيوتر والهاتف نوجّهه دائماً للقاعدة المحلية
    # (رابط Render/Neon القديم يُستخدم فقط في ops/import_render.py لنقل البيانات)
    if not os.environ.get('DATABASE_URL', '').startswith('sqlite'):
        os.environ['DATABASE_URL'] = 'sqlite:///' + os.environ['BELL_DB_PATH'].replace('\\', '/')


def data_dir():
    return os.environ.get('DATA_DIR') or os.path.join(BASE, 'data')


def db_path():
    return os.environ.get('BELL_DB_PATH') or os.path.join(data_dir(), 'bell.db')


def uploads_dir():
    """الشعارات والخلفيات والنغمات محفوظة داخل القاعدة نفسها — لا مجلد مرفقات"""
    return os.path.join(data_dir(), 'uploads')


def backup_root():
    return os.environ.get('BACKUP_DIR') or os.path.join(BASE, 'backups')


def stamp():
    """وقت عمّان (للترتيب ولأسماء الملفات)"""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Amman')).strftime('%Y-%m-%d_%H%M%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d_%H%M%S')


def die(msg, code=1):
    print('\n✖ ' + msg)
    sys.exit(code)
