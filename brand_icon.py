"""أيقونة تبويب المتصفح (favicon) الخاصة بالعميل — اختيارية.

إذا ضُبط في ملف `.env` المتغير FAVICON_FILE بمسار ملف PNG مربع (512×512 مثلاً) وكان الملف موجوداً،
حلّت هذه الأيقونة محل أيقونة النظام الافتراضية في تبويب المتصفح. وإذا لم يُضبط أو فُقد الملف بقيت
الأيقونة الافتراضية دون أي خطأ. يُقرأ المتغير عند كل طلب فيسري تغيير الملف دون إعادة تشغيل.
"""
import os

from flask import abort, send_file


def _path():
    p = (os.environ.get('FAVICON_FILE') or '').strip()
    return p if p and os.path.isfile(p) else ''


def install(app):
    @app.route('/brand-favicon.png', endpoint='brand_favicon')
    def brand_favicon():
        p = _path()
        if not p:
            abort(404)
        return send_file(p, mimetype='image/png', max_age=3600)

    @app.context_processor
    def _favicon_context():
        p = _path()
        if not p:
            return {'favicon_url': ''}
        try:
            v = int(os.path.getmtime(p))
        except OSError:
            v = 0
        return {'favicon_url': f'/brand-favicon.png?v={v}'}
