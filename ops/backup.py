"""نسخة احتياطية: قاعدة البيانات (SQLite) + نسخة من الكود (الشعارات والنغمات داخل القاعدة).

    python ops/backup.py                 # إلى BACKUP_DIR (مجلد Google Drive) أو backups
    python ops/backup.py --tag phone     # من الهاتف (اسم الملف ينتهي بـ -phone)
    python ops/backup.py --dir PATH --no-code --no-uploads

• القاعدة: BACKUP_DIR/db/bell-<وقت>.db.gz  (آخر 48 نسخة من كل نوع)
• الكود: BACKUP_DIR/code/bell-code.zip  (احتياط لتحديث الهاتف بدون GitHub — بدون .env والبيانات)
"""
import argparse
import glob
import gzip
import os
import shutil
import sqlite3
from contextlib import closing
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, backup_root, db_path, die, load_env, stamp, t, uploads_dir  # noqa: E402


def _to_dest(local_file, dest_file):
    """نسخ ملف جاهز إلى مجلد النسخ مرة واحدة. Google Drive يقفل الملفات الجديدة فوراً للمزامنة،
    لذلك لا نُنشئ ملفات مؤقتة داخله ولا نعيد تسميتها هناك — نحاول عدة مرات إن كان مشغولاً."""
    import time
    for attempt in range(6):
        try:
            shutil.copyfile(local_file, dest_file)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(5)


def _neon_push(gz):
    """نسخة ثانية في Neon (إن كان NEON_BACKUP_URL في .env) — لا تُفشل النسخ إن تعذّرت"""
    try:
        import neon_store
        if neon_store._url():
            neon_store.push(gz)
    except BaseException as e:   # noqa: BLE001  (SystemExit من neon_store أيضاً)
        print(t(f'⚠ Neon: تعذّر الرفع ({e})', f'⚠ Neon: upload failed ({e})'))


def backup_db(dest_dir, tag, keep):
    import tempfile
    src = db_path()
    if not os.path.isfile(src):
        die(t(f'لا توجد قاعدة بيانات بعد: {src}', f'No database yet: {src}'))
    os.makedirs(dest_dir, exist_ok=True)
    name = f'bell-{stamp()}{"-" + tag if tag else ""}.db'
    final = os.path.join(dest_dir, name + '.gz')
    with tempfile.TemporaryDirectory() as tmpd:
        raw = os.path.join(tmpd, name)
        # نسخة متسقة حتى أثناء عمل الموقع (sqlite backup API) — داخل مجلد مؤقت محلي
        with closing(sqlite3.connect(src, timeout=60)) as s, closing(sqlite3.connect(raw)) as d:
            s.backup(d)
        gz = raw + '.gz'
        with open(raw, 'rb') as fi, gzip.open(gz, 'wb', compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        _to_dest(gz, final)
        _neon_push(gz)
    # الاحتفاظ بآخر keep نسخة من نفس النوع
    pattern = '*-phone.db.gz' if tag == 'phone' else 'bell-*.db.gz'
    files = sorted(f for f in glob.glob(os.path.join(dest_dir, pattern)) if tag == 'phone' or not f.endswith('-phone.db.gz'))
    for old in files[:-keep] if keep > 0 else []:
        try:
            os.remove(old)
        except OSError:
            pass
    for junk in glob.glob(os.path.join(dest_dir, '*.tmp')) + glob.glob(os.path.join(dest_dir, '*.part')):
        try:
            os.remove(junk)   # بقايا محاولات سابقة
        except OSError:
            pass
    return final


def mirror(src, dst):
    n = 0
    if not os.path.isdir(src):
        return 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out = os.path.join(dst, rel) if rel != '.' else dst
        os.makedirs(out, exist_ok=True)
        for f in files:
            a, b = os.path.join(root, f), os.path.join(out, f)
            if not os.path.exists(b) or os.path.getsize(a) != os.path.getsize(b) or os.path.getmtime(a) > os.path.getmtime(b) + 2:
                shutil.copy2(a, b)
                n += 1
    return n


def code_zip(dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    skip_dirs = {'.git', 'venv', '.venv', 'data', 'uploads', 'backups', 'logs', '__pycache__', 'node_modules', 'mobile', 'desktop', 'native', 'player_cache'}
    import tempfile
    out = os.path.join(dest_dir, 'bell-code.zip')
    tmpd = tempfile.mkdtemp()
    local = os.path.join(tmpd, 'bell-code.zip')
    with zipfile.ZipFile(local, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f in ('.env', 'pyexe.txt', '.secret_key') or f.endswith(('.db', '.db-wal', '.db-shm', '.pyc')):
                    continue
                full = os.path.join(root, f)
                arc = os.path.join('bell', os.path.relpath(full, BASE))
                if f.endswith('.sh'):   # الهاتف يحتاج نهايات أسطر LF
                    z.writestr(arc, open(full, 'rb').read().replace(b'\r\n', b'\n'))
                else:
                    z.write(full, arc)
    _to_dest(local, out)
    shutil.rmtree(tmpd, ignore_errors=True)


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='')
    ap.add_argument('--tag', default='')
    ap.add_argument('--keep', type=int, default=48)
    ap.add_argument('--no-code', action='store_true')
    ap.add_argument('--no-uploads', action='store_true')
    a = ap.parse_args()
    root = a.dir or backup_root()
    f = backup_db(os.path.join(root, 'db'), a.tag, a.keep)
    print(t('✓ القاعدة: ', '✓ Database: ') + f)
    if not a.no_uploads:
        n = mirror(uploads_dir(), os.path.join(root, 'uploads'))
        print(t(f'✓ المرفقات: {n} ملف جديد', f'✓ Uploads: {n} new files'))
    if not a.no_code:
        code_zip(os.path.join(root, 'code'))
        print(t('✓ الكود', '✓ Code'))


if __name__ == '__main__':
    main()
