"""استرجاع نسخة احتياطية (أوقف الموقع قبلها — ملفات .bat وزر الهاتف يفعلان ذلك تلقائياً).

    python ops/restore.py --latest auto --yes                # أحدث نسخة في BACKUP_DIR
    python ops/restore.py --latest auto --tag phone --yes    # أحدث نسخة رفعها الهاتف
    python ops/restore.py --latest DIR --uploads --yes       # من مجلد آخر + المرفقات
    python ops/restore.py --file PATH.db.gz --yes

• قبل الاستبدال تُحفظ القاعدة الحالية في data/before-restore-<وقت>.db (آخر 5).
• --since-marker: مع --tag phone يرفض نسخة الهاتف إن كانت أقدم من وقت تشغيل الطوارئ (لم تُرفع بعد).
"""
import argparse
import glob
import gzip
import os
import shutil
import sqlite3
from contextlib import closing
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backup import mirror  # noqa: E402
from common import backup_root, data_dir, db_path, die, load_env, stamp, t, uploads_dir  # noqa: E402


def pick(root, tag):
    files = glob.glob(os.path.join(root, 'db', 'bell-*.db.gz'))
    if tag == 'phone':
        files = [f for f in files if f.endswith('-phone.db.gz')]
    files.sort(key=lambda f: os.path.basename(f)[5:22])   # الترتيب بالوقت المكتوب في الاسم
    return files[-1] if files else None


def file_stamp(path):
    return os.path.basename(path)[5:22]   # bell-YYYY-MM-DD_HHMMSS…


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument('--latest', default='')
    ap.add_argument('--file', default='')
    ap.add_argument('--tag', default='')
    ap.add_argument('--uploads', action='store_true')
    ap.add_argument('--since-marker', action='store_true')
    ap.add_argument('--yes', action='store_true')
    a = ap.parse_args()
    root = backup_root() if a.latest in ('', 'auto') else a.latest
    src = a.file or pick(root, a.tag)
    if not src or not os.path.isfile(src):
        die(t(f'لا توجد نسخة احتياطية في {root}/db', f'No backup found in {root}/db'))
    if a.since_marker:
        mk = os.path.join(root, 'db', 'ACTIVE_SITE.txt')
        if os.path.isfile(mk):
            parts = open(mk, encoding='utf-8', errors='ignore').read().split()
            if len(parts) >= 2 and parts[0] == 'phone' and file_stamp(src) < parts[1]:
                die(t(f'نسخة الهاتف ({file_stamp(src)}) أقدم من بدء الطوارئ ({parts[1]}) — ارفع بيانات الهاتف أولاً (زر ATT 5) وانتظر مزامنة Drive دقيقة',
                      f'The phone backup ({file_stamp(src)}) is older than the emergency start ({parts[1]}) - upload the phone data first (ATT 5) and wait a minute for Drive to sync'))
    print(t(f'النسخة: {src}', f'Backup: {src}'))
    if not a.yes and input(t('ستُستبدل كل بيانات هذا الجهاز. اكتب YES: ', 'All data on this device will be replaced. Type YES: ')).strip() != 'YES':
        die(t('تم الإلغاء', 'Cancelled'))

    os.makedirs(data_dir(), exist_ok=True)
    tmp = db_path() + '.restore'
    with gzip.open(src, 'rb') as fi, open(tmp, 'wb') as fo:
        shutil.copyfileobj(fi, fo)
    try:
        with closing(sqlite3.connect(tmp)) as c:
            ok = c.execute('PRAGMA integrity_check').fetchone()[0]
            n = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    except sqlite3.DatabaseError as e:
        os.remove(tmp)
        die(t(f'النسخة تالفة: {e}', f'The backup is damaged: {e}'))
    if ok != 'ok' or n == 0:
        os.remove(tmp)
        die(t('النسخة تالفة أو فارغة — لم يتغيّر شيء', 'The backup is damaged or empty - nothing changed'))

    cur = db_path()
    if os.path.isfile(cur):
        safe = os.path.join(data_dir(), f'before-restore-{stamp()}.db')
        with closing(sqlite3.connect(cur, timeout=60)) as s, closing(sqlite3.connect(safe)) as d:
            s.backup(d)
        for old in sorted(glob.glob(os.path.join(data_dir(), 'before-restore-*.db')))[:-5]:
            os.remove(old)
        print(t(f'✓ نسخة أمان من البيانات السابقة: {safe}', f'✓ Safety copy of the previous data: {safe}'))
    for ext in ('-wal', '-shm'):
        if os.path.exists(cur + ext):
            os.remove(cur + ext)
    os.replace(tmp, cur)
    print(t(f'✓ استُرجعت القاعدة ({n} جدول)', f'✓ Database restored ({n} tables)'))
    if a.uploads:
        k = mirror(os.path.join(root, 'uploads'), uploads_dir())
        print(t(f'✓ المرفقات: {k} ملف', f'✓ Uploads: {k} files'))


if __name__ == '__main__':
    main()
