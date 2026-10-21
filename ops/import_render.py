"""نقل كل بيانات جرس الحصص من الخادم القديم (Render/Neon — PostgreSQL) إلى قاعدة هذا الكمبيوتر (SQLite).

    python ops/import_render.py                       # يسأل عن رابط القاعدة القديمة
    python ops/import_render.py "postgresql://..." --yes
    python ops/import_render.py C:\\old\\bell.db --yes   # أو من ملف bell.db قديم

• الرابط: من لوحة Render ← قاعدة البيانات ← Connections ← «External Database URL» (أو رابط Neon).
• يُنقل كل شيء كما هو بنفس الأرقام: المراحل والحصص والبرامج والصفوف والشعب والمعلمون **والأكواد** (فتبقى
  الأجهزة والتطبيقات المسجلة تعمل بنفس أكوادها) والشعارات والخلفيات والنغمات وسجل الأجهزة والإعدادات.
• قبل الاستبدال تُحفظ القاعدة الحالية في data/before-import-<وقت>.db إن كان فيها بيانات.
• لا يغيّر شيئاً في الخادم القديم (قراءة فقط).
"""
import os
import sqlite3
import sys
from contextlib import closing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, data_dir, db_path, die, load_env, stamp, t  # noqa: E402


def main():
    load_env()
    sys.path.insert(0, BASE)
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    src = args[0] if args else input(t('  الصق رابط قاعدة Render/Neon القديمة (External Database URL)، أو مسار ملف bell.db: ',
                                       '  Paste the old Render/Neon database URL (External Database URL), or a bell.db path: ')).strip().strip('"')
    if not src:
        die(t('تم الإلغاء', 'Cancelled'))
    from app import create_app, normalize_db_url
    from sqlalchemy import create_engine, inspect, select, text
    if os.path.isfile(src):
        src_url = 'sqlite:///' + os.path.abspath(src).replace('\\', '/')
    else:
        src_url = normalize_db_url(src)
        if not src_url.startswith('postgresql'):
            die(t('الرابط يجب أن يبدأ بـ postgres:// أو postgresql://', 'The URL must start with postgres:// or postgresql://'))
        if 'sslmode=' not in src_url and 'localhost' not in src_url:
            src_url += ('&' if '?' in src_url else '?') + 'sslmode=require'
    try:
        src_eng = create_engine(src_url, pool_pre_ping=True)
        with src_eng.connect() as c:
            c.execute(text('SELECT 1'))
    except Exception as e:   # noqa: BLE001
        die(t(f'تعذّر الاتصال بالقاعدة القديمة: {e}', f'Could not connect to the old database: {e}'))

    app = create_app()                       # يُنشئ جداول القاعدة المحلية (data/bell.db)
    from models import db
    src_tables = set(inspect(src_eng).get_table_names())
    if 'stages' not in src_tables:
        die(t('القاعدة القديمة لا تحتوي جداول جرس الحصص — تأكد من الرابط', 'The old database has no bell tables - check the URL'))

    with app.app_context():
        dst = db.engine
        tables = db.metadata.sorted_tables
        counts = {}
        with dst.connect() as c:
            have = sum(c.execute(select(text('count(*)')).select_from(tb)).scalar() or 0 for tb in tables)
        print(t(f'  القاعدة المحلية: {db_path()}', f'  Local database: {db_path()}'))
        if have:
            if '--yes' not in sys.argv and input(t(f'  فيها {have} سجل سيُستبدل بالكامل. اكتب YES: ', f'  It has {have} rows that will be replaced. Type YES: ')).strip() != 'YES':
                die(t('تم الإلغاء', 'Cancelled'))
            safe = os.path.join(data_dir(), f'before-import-{stamp()}.db')
            db.session.remove()
            with closing(sqlite3.connect(db_path(), timeout=60)) as s, closing(sqlite3.connect(safe)) as d:
                s.backup(d)
            print(t(f'  ✓ نسخة أمان: {safe}', f'  ✓ Safety copy: {safe}'))
        src_insp = inspect(src_eng)
        with src_eng.connect() as sc, dst.begin() as dc:
            for tb in reversed(tables):
                dc.execute(tb.delete())
            for tb in tables:
                if tb.name not in src_tables:
                    counts[tb.name] = 0
                    continue
                cols_src = {c['name'] for c in src_insp.get_columns(tb.name)}
                cols = [c for c in tb.columns if c.name in cols_src]
                rows = [dict(r._mapping) for r in sc.execute(select(*[tb.c[c.name] for c in cols]))]
                for i in range(0, len(rows), 500):
                    dc.execute(tb.insert(), rows[i:i + 500])
                counts[tb.name] = len(rows)
    for name, n in counts.items():
        print(f'  {name:28} {n}')
    print(t('\n✅ نُقلت كل البيانات. الأجهزة والتطبيقات تعمل بنفس أكوادها بعد توجيهها للعنوان الجديد.',
            '\n✅ All data copied. Devices and apps keep their codes once they point to the new address.'))


if __name__ == '__main__':
    main()
