"""نسخة سطح المكتب (BellDesktop.exe): صفحة الترخيص وحالته. لا تُسجَّل إلا عند BELL_EDITION=desktop (انظر app.py).

مشغّل الصوت المضمَّن (bell_player/bell_player.py) يتحقق من الترخيص نفسه: مفتاح موقّع (ECDSA P-256) مرتبط ببصمة هذا
الكمبيوتر، ومن دونه لا يرنّ الجرس. وحدة المشغّل يضعها المشغّل التنفيذي في `app.config['BELL_PLAYER']`.
"""
import threading

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from blueprints.admin import _msg, login_required

desktop_bp = Blueprint('desktop', __name__)
_lock = threading.Lock()


def player():
    return current_app.config.get('BELL_PLAYER')


def fingerprint():
    """بصمة هذا الكمبيوتر (تُحسب مرة واحدة لأن حسابها يستدعي أوامر ويندوز)."""
    with _lock:
        fp = current_app.config.get('BELL_FP')
        if not fp and player():
            fp = current_app.config['BELL_FP'] = player().machine_fingerprint()
        return fp or ''


def license_state():
    """→ {'ok', 'expired', 'exp', 'ready'}: ready=False قبل أن تُحسب البصمة (لا يُعرض تنبيه مبكر خاطئ)."""
    bp = player()
    fp = current_app.config.get('BELL_FP')
    if not bp or not fp:
        return {'ok': False, 'expired': False, 'exp': None, 'ready': False}
    key = bp.load_ini().get('license', '')
    ok, expired, exp = bp.check_license(key, fp, bp.amman_now().date())
    return {'ok': ok and not expired, 'expired': bool(ok and expired), 'exp': exp, 'ready': True}


@desktop_bp.app_context_processor
def _inject():
    return {'desktop_edition': True, 'desktop_lic': license_state()}


@desktop_bp.route('/admin/license')
@login_required
def page():
    return render_template('admin/desktop_license.html', fp=fingerprint(), lic=license_state())


@desktop_bp.route('/admin/license/save', methods=['POST'])
@login_required
def save():
    bp = player()
    key = (request.form.get('key') or '').strip()
    fp = fingerprint()
    if not bp or not fp:
        flash(_msg('مشغّل الصوت غير متاح.', 'The sound player is not available.'), 'error')
        return redirect(url_for('desktop.page'))
    ok, expired, _exp = bp.check_license(key, fp, bp.amman_now().date())
    if not ok:
        flash(_msg('المفتاح غير صالح لهذا الكمبيوتر. تأكد من نسخه كاملاً، ومن أنه صدر لهذه البصمة.',
                   'The key is not valid for this computer. Copy it completely and check it was issued for this fingerprint.'), 'error')
    elif expired:
        flash(_msg('انتهى هذا المفتاح. اطلب مفتاحاً جديداً.', 'This key has expired. Ask for a new one.'), 'error')
    else:
        cfg = bp.load_ini()
        bp.save_ini(cfg['server'], cfg['key'], license_key=key)       # المشغّل يقرأ الملف كل 30 ثانية فيبدأ الرنين
        flash(_msg('حُفظ الترخيص. سيبدأ الجرس بالرنين خلال لحظات.', 'The license is saved. The bell will start ringing in a moment.'), 'ok')
    return redirect(url_for('desktop.page'))
