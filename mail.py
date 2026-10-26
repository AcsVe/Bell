"""Email through Microsoft Graph — the same app registration as the other iajaward.org systems (venues, SAS).

The four values come from .env, written by ops/setup_local.py (1_setup.bat):
    MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_SENDER_EMAIL
They are never shown in the interface: Settings only shows whether each one is set.

Every attempt is written to ``email_logs`` with its error and the result is returned to the caller.
Tests set MAIL_BACKEND='memory' and read MAIL_OUTBOX instead of calling Graph.
"""
import time

import requests
from flask import current_app

from i18n import pick

_token_cache = {'token': None, 'expires_at': 0, 'key': None}
KEYS = ('MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET', 'MS_SENDER_EMAIL')


class MailResult:
    def __init__(self, ok, status, error=None, recipients=None):
        self.ok, self.status, self.error, self.recipients = ok, status, error, recipients or []

    def __bool__(self):
        return self.ok


def graph_status():
    """Which settings are present (booleans only) and the sender address — never a secret."""
    cfg = current_app.config
    st = {'tenant': bool(cfg.get('MS_TENANT_ID')), 'client': bool(cfg.get('MS_CLIENT_ID')),
          'secret': bool(cfg.get('MS_CLIENT_SECRET')), 'sender': cfg.get('MS_SENDER_EMAIL') or ''}
    st['ready'] = st['tenant'] and st['client'] and st['secret'] and bool(st['sender'])
    return st


def _graph_token():
    cfg = current_app.config
    tenant, cid, secret = cfg.get('MS_TENANT_ID'), cfg.get('MS_CLIENT_ID'), cfg.get('MS_CLIENT_SECRET')
    if not (tenant and cid and secret):
        raise RuntimeError(pick('إعدادات Microsoft Graph ناقصة (MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET)',
                                'Microsoft Graph settings are incomplete (MS_TENANT_ID / MS_CLIENT_ID / MS_CLIENT_SECRET)'))
    now = time.time()
    key = (tenant, cid)
    if _token_cache['token'] and _token_cache['key'] == key and now < _token_cache['expires_at'] - 60:
        return _token_cache['token']
    r = requests.post(f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
                      data={'client_id': cid, 'client_secret': secret,
                            'scope': 'https://graph.microsoft.com/.default', 'grant_type': 'client_credentials'},
                      timeout=15)
    if r.status_code != 200:
        raise RuntimeError(pick('تعذّر الحصول على رمز Graph', 'Could not get a Graph token') + f': HTTP {r.status_code} {r.text[:300]}')
    data = r.json()
    _token_cache.update(token=data['access_token'], key=key, expires_at=now + int(data.get('expires_in', 3600)))
    return _token_cache['token']


def _graph_send(sender, to_list, subject, html):
    token = _graph_token()
    message = {'subject': subject, 'body': {'contentType': 'HTML', 'content': html},
               'toRecipients': [{'emailAddress': {'address': e}} for e in to_list]}
    r = requests.post(f'https://graph.microsoft.com/v1.0/users/{sender}/sendMail',
                      headers={'Authorization': f'Bearer {token}'},
                      json={'message': message, 'saveToSentItems': True}, timeout=20)
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f'Graph sendMail HTTP {r.status_code}: {r.text[:300]}')


def split_emails(value):
    import re
    return [e for e in re.split(r'[\s,;]+', value or '') if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', e)]


def send_email(recipients, subject, html, kind=None):
    """Send one HTML email; always logs the attempt (commits the log row)."""
    from models import db, EmailLog
    to_list = split_emails(','.join(recipients) if isinstance(recipients, (list, tuple, set)) else recipients)
    log = EmailLog(recipients=', '.join(to_list)[:1000] or '-', subject=subject[:500], kind=kind, status='failed')
    if not to_list:
        log.status, log.error = 'skipped', pick('لا يوجد بريد صالح للمستلم', 'No valid recipient address')
        result = MailResult(False, 'skipped', log.error)
    else:
        try:
            if current_app.config.get('MAIL_BACKEND', 'graph') == 'memory':
                current_app.config.setdefault('MAIL_OUTBOX', []).append(
                    {'to': to_list, 'subject': subject, 'html': html, 'kind': kind})
            else:
                sender = current_app.config.get('MS_SENDER_EMAIL')
                if not sender:
                    raise RuntimeError(pick('بريد المرسل (MS_SENDER_EMAIL) غير مضبوط', 'The sender (MS_SENDER_EMAIL) is not set'))
                _graph_send(sender, to_list, subject, html)
            log.status = 'sent'
            result = MailResult(True, 'sent', recipients=to_list)
        except Exception as e:  # recorded and returned, never swallowed
            log.error = str(e)[:2000]
            current_app.logger.error('[mail] %s -> %s: %s', subject, to_list, e)
            result = MailResult(False, 'failed', str(e), to_list)
    db.session.add(log)
    db.session.commit()
    return result
