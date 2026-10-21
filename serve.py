"""تشغيل نظام جرس الحصص على كمبيوتر المدرسة أو الهاتف بخادم Waitress (يعمل على Windows وTermux).

    python serve.py

• العنوان المحلي: http://localhost:8005  (PORT في .env) — والعام عبر Cloudflare: https://5.iajaward.org
• البيانات: مجلد data (القاعدة bell.db، وفيها الشعارات والخلفيات والنغمات) — DATA_DIR في .env لتغييره
• السجل: logs/server.log
"""
import logging
import logging.handlers
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'ops'))
from common import load_env  # noqa: E402

load_env()
os.makedirs(os.path.join(BASE, 'logs'), exist_ok=True)
handler = logging.handlers.RotatingFileHandler(os.path.join(BASE, 'logs', 'server.log'),
                                               maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
log = logging.getLogger('bell')


def main():
    from app import app
    from waitress import serve
    port = int(os.environ.get('PORT') or 8005)
    log.info('BELL running on http://localhost:%s  (site: %s, db: %s)', port,
             os.environ.get('BASE_URL') or '-', os.environ.get('BELL_DB_PATH'))
    serve(
        app,
        host=os.environ.get('HOST', '0.0.0.0'),
        port=port,
        threads=int(os.environ.get('THREADS') or 8),
        trusted_proxy='127.0.0.1',            # Cloudflare Tunnel يتصل من نفس الجهاز ويمرّر https
        trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-host', 'x-forwarded-for'},
        clear_untrusted_proxy_headers=True,
        max_request_body_size=64 * 1024 ** 2,
        channel_timeout=300,
        ident='BELL',
    )


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log.exception('Server failed to start')
        raise
