"""حماية مصدر الصفحات: كل صفحة HTML كاملة تُرسل مشفّرة، ويفكّها سطر صغير داخل المتصفح.

• «عرض المصدر» يُظهر كتلة رموز فقط بدل الكود والنصوص.
• يمنع أيضاً الزر الأيمن واختصارات عرض المصدر/الحفظ/أدوات المطوّر (إلا داخل خانات الكتابة).
• يشمل لوحة الإدارة وصفحات الشاشات. لا يلمس ردود JSON والملفات والنغمات وأجزاء HTML التي تُطلب بالجافاسكربت.
• للتعطيل مؤقتاً: PAGE_SHIELD=0 في .env — ولإيقاف كشف أدوات المطوّر فقط: DEVTOOLS_GUARD=0
• كشف أدوات المطوّر (لوحة الإدارة فقط): إن كانت مفتوحة لا يظهر المحتوى، وإن فُتحت أثناء التصفح تُمسح الصفحة خلال ثانية.
  لا يُفعَّل على شاشات الصفوف (/app) حتى لا تُمسح شاشة على جهاز بطيء بالخطأ.

ملاحظة صريحة: لا شيء يخفي الكود نهائياً عن خبير — هذا يجعله غير مقروء لعامة الناس.
"""
import base64
import os
import random

_GUARD = (b"<script>(function(){function ed(e){return e&&e.closest&&e.closest('input,textarea,select,[contenteditable=\"true\"]');}"
          b"document.addEventListener('contextmenu',function(e){if(!ed(e.target))e.preventDefault();});"
          b"document.addEventListener('keydown',function(e){var k=(e.key||'').toLowerCase(),c=e.ctrlKey||e.metaKey;"
          b"if(e.key==='F12'||(c&&e.shiftKey&&k&&'ijck'.indexOf(k)>-1)||(e.metaKey&&e.altKey&&k&&'ijcu'.indexOf(k)>-1)"
          b"||(c&&!e.shiftKey&&(k==='u'||k==='s'))){e.preventDefault();e.stopPropagation();return false;}},true);"
          b"document.addEventListener('dragstart',function(e){if(e.target&&e.target.tagName==='IMG')e.preventDefault();});})();</script>")

_B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'


def _encode(html_bytes):
    """base64 ← إزاحة عشوائية في الأبجدية ← عكس النص (يتغير شكل الكتلة مع كل طلب)"""
    s = base64.b64encode(html_bytes).decode('ascii')
    k = random.randint(1, 63)
    table = str.maketrans(_B64, _B64[k:] + _B64[:k])
    return s.translate(table)[::-1], k


_BLOCK = ('<div style="font-family:Tahoma,Arial,sans-serif;direction:rtl;text-align:center;padding:80px 20px;color:#333">'
          '<div style="font-size:48px">&#128274;</div><h2 style="margin:16px 0 8px">غير مسموح</h2>'
          '<p>أدوات المطوّر غير مسموح بها في هذا الموقع.<br>أغلقها ثم أعد تحميل الصفحة.</p>'
          '<p style="color:#888;font-size:13px">Developer tools are not allowed on this site. Close them and reload.</p></div>')


def _shell(payload, k, guard_devtools=True):
    # الفك أثناء تحميل الصفحة (document.write داخل head) فتعمل كل السكربتات والأحداث كالمعتاد.
    # كشف أدوات المطوّر: عبارة debugger لا تفعل شيئاً والأدوات مغلقة (لا إنذار خاطئ مع التكبير أو صغر النافذة)،
    # وعندما تكون مفتوحة يتوقف التنفيذ عندها — فنعرف ونمسح الصفحة.
    dt = ('function O(){var t=performance.now();debugger;return performance.now()-t>120;}'
          'function X(){try{document.documentElement.innerHTML="<head><meta charset=utf-8></head><body>"+B+"</body>";}catch(e){}}'
          'if(O()){document.write(B);return;}') if guard_devtools else 'function O(){return false;}function X(){}'
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<script>(function(){var A="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",'
            f'B={_BLOCK!r};' + dt +
            f'var k={k},p="{payload}".split("").reverse().join(""),o="";'
            'for(var i=0;i<p.length;i++){var c=p.charAt(i),j=A.indexOf(c);o+=j<0?c:A.charAt((j-k+64)%64);}'
            'var b=atob(o),u=new Uint8Array(b.length);for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);'
            'document.write(new TextDecoder("utf-8").decode(u));'
            'var iv=setInterval(function(){if(O()){clearInterval(iv);X();}},1000);})();</script>'
            '</head><body></body></html>').encode('utf-8')


def install(app):
    from flask import request

    @app.after_request
    def _page_shield(resp):
        if os.environ.get('PAGE_SHIELD', '1') == '0':
            return resp
        if app.testing and not app.config.get('PAGE_SHIELD'):
            return resp
        try:
            if resp.mimetype != 'text/html' or resp.direct_passthrough or resp.is_streamed:
                return resp
            body = resp.get_data()
            head = body[:400].lstrip().lower()
            if not (head.startswith(b'<!doctype') or head.startswith(b'<html')):
                return resp          # جزء HTML يُطلب بالجافاسكربت — يبقى كما هو
            i = body.rfind(b'</body>')
            body = body[:i] + _GUARD + body[i:] if i != -1 else body + _GUARD
            payload, k = _encode(body)
            guard = os.environ.get('DEVTOOLS_GUARD', '1') != '0' and request.path.startswith('/admin')
            resp.set_data(_shell(payload, k, guard))
            resp.headers['Cache-Control'] = 'no-store'
        except Exception:
            pass
        return resp
