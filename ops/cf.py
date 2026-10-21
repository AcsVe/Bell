"""ربط 5.iajaward.org (جرس الحصص) بالكمبيوتر أو بالهاتف عبر Cloudflare API (بدون لوحة Cloudflare).

    python ops/cf.py setup      # مرة واحدة: سجل DNS + ربط الاسم بنفق السيرفر + إنشاء نفق الهاتف bell-standby
    python ops/cf.py status     # أين يعمل 5.iajaward.org الآن + تشخيص
    python ops/cf.py phone      # 5.iajaward.org ← الهاتف
    python ops/cf.py server     # 5.iajaward.org ← الكمبيوتر
    python ops/cf.py auto       # الحارس (كل 5 دقائق على الكمبيوتر): يعيد الموقع للكمبيوتر إن كان على هاتف منقطع
    python ops/cf.py server-up  # رمز خروج 0 إذا كان الكمبيوتر متصلاً
    python ops/cf.py token      # (للهاتف) يكتب رمز نفق الهاتف — لا تعرضه لأحد

في .env:  CF_API_TOKEN=...   (نفس رمز iaj-switch: Zone:Read + DNS:Edit + Cloudflare Tunnel:Edit)
اختياري:  CF_ZONE=iajaward.org  CF_HOSTS=5.iajaward.org  CF_MAIN_TUNNEL=iajaward  CF_STANDBY_TUNNEL=bell-standby  PORT=8005
"""
import base64
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BASE, EN, die, load_env, t  # noqa: E402

API = 'https://api.cloudflare.com/client/v4'


class CF:
    def __init__(self, token):
        import requests
        self.s = requests.Session()
        self.s.headers.update({'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})

    def call(self, method, path, **kw):
        r = self.s.request(method, API + path, timeout=30, **kw)
        try:
            data = r.json()
        except ValueError:
            die(t(f'رد غير متوقع من Cloudflare ({r.status_code})', f'Unexpected reply from Cloudflare ({r.status_code})'))
        if not data.get('success'):
            errs = '; '.join(e.get('message', '') for e in data.get('errors', []))
            die(t(f'Cloudflare رفض الطلب: {errs or r.status_code}\n  (تأكد من صلاحيات رمز API)', f'Cloudflare refused: {errs or r.status_code}\n  (check the API token permissions)'))
        return data.get('result')


def probe(host):
    """يفتح الموقع فعلاً عبر الإنترنت ويعيد رمز الاستجابة (أو None)"""
    import requests
    try:
        return requests.get(f'https://{host}/', timeout=15, allow_redirects=False,
                            headers={'User-Agent': 'bell-status'}).status_code
    except Exception:
        return None


def site_answers(host, tries=3):
    """هل يجيب نظام الجرس فعلاً على https://host ؟ (خطأ 5xx/530 من Cloudflare = الكمبيوتر لا يعمل)"""
    import urllib.request
    for i in range(tries):
        try:
            req = urllib.request.Request(f'https://{host}/healthz?probe={int(time.time())}',
                                         headers={'User-Agent': 'bell-standby-check', 'Cache-Control': 'no-cache'})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200 and r.read(10).decode('utf-8', 'replace').strip() == 'ok':
                    return True
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(3)
    return False


def up(tun):
    return (tun.get('status') or '').lower() in ('healthy', 'degraded')


def main():
    load_env()
    cmd = (sys.argv[1] if len(sys.argv) > 1 else 'status').lower()
    if cmd not in ('setup', 'status', 'phone', 'server', 'auto', 'server-up', 'token'):
        print(__doc__)
        return
    token = os.environ.get('CF_API_TOKEN', '').strip()
    if not token:
        die(t('لا يوجد CF_API_TOKEN في ملف .env', 'No CF_API_TOKEN in .env'))
    zone_name = os.environ.get('CF_ZONE', 'iajaward.org')
    from urllib.parse import urlparse
    base_host = urlparse(os.environ.get('BASE_URL', '')).hostname or ''
    hosts = [h.strip() for h in os.environ.get('CF_HOSTS', base_host if base_host.endswith('.' + zone_name) and base_host != 'www.' + zone_name else '5.' + zone_name).split(',') if h.strip()]
    main_name = os.environ.get('CF_MAIN_TUNNEL', 'iajaward')
    sb_name = os.environ.get('CF_STANDBY_TUNNEL', 'bell-standby')
    service = f"http://localhost:{os.environ.get('PORT', '8005')}"
    H = ', '.join(hosts)
    cf = CF(token)

    zones = cf.call('GET', '/zones', params={'name': zone_name})
    if not zones:
        die(t(f'لم أجد النطاق {zone_name}', f'Zone {zone_name} not found'))
    zone, acc = zones[0]['id'], zones[0]['account']['id']

    def tunnel(name, create=False):
        res = cf.call('GET', f'/accounts/{acc}/cfd_tunnel', params={'name': name, 'is_deleted': 'false'})
        if res:
            return res[0]
        if not create:
            die(t(f'لم أجد النفق «{name}» — شغّل أولاً على الكمبيوتر: python ops/cf.py setup', f'Tunnel "{name}" not found - run on the PC first: python ops/cf.py setup'))
        secret = base64.b64encode(secrets.token_bytes(32)).decode()
        print(t(f'✓ أُنشئ نفق الهاتف «{name}»', f'✓ Created the phone tunnel "{name}"'))
        return cf.call('POST', f'/accounts/{acc}/cfd_tunnel', json={'name': name, 'config_src': 'cloudflare', 'tunnel_secret': secret})

    def ensure_ingress(tun):
        res = cf.call('GET', f"/accounts/{acc}/cfd_tunnel/{tun['id']}/configurations") or {}
        cfg = res.get('config') or {}
        rules = cfg.get('ingress') or []
        named = [r for r in rules if r.get('hostname')]
        catch = [r for r in rules if not r.get('hostname')] or [{'service': 'http_status:404'}]
        changed = False
        for h in hosts:
            cur = next((r for r in named if r.get('hostname') == h), None)
            if cur is None:
                named.append({'hostname': h, 'service': service})
                changed = True
            elif cur.get('service') != service:
                cur['service'] = service
                changed = True
        if changed:
            cfg['ingress'] = named + catch[-1:]
            cf.call('PUT', f"/accounts/{acc}/cfd_tunnel/{tun['id']}/configurations", json={'config': cfg})
            print(t(f'✓ نفق «{tun["name"]}»: {", ".join(hosts)} ← {service}', f'✓ Tunnel "{tun["name"]}": {", ".join(hosts)} -> {service}'))

    def records():
        out = {}
        for h in hosts:
            rec = cf.call('GET', f'/zones/{zone}/dns_records', params={'name': h})
            if rec:
                out[h] = rec[0]
        return out

    if cmd == 'token':
        sb = tunnel(sb_name)
        sys.stdout.write(cf.call('GET', f"/accounts/{acc}/cfd_tunnel/{sb['id']}/token"))
        return

    t_main = tunnel(main_name)
    if cmd == 'setup':
        t_sb = tunnel(sb_name, create=True)
        ensure_ingress(t_main)
        ensure_ingress(t_sb)
        recs = records()
        for h in hosts:
            rec = recs.get(h)
            target = f"{t_main['id']}.cfargotunnel.com"
            if rec is None:
                cf.call('POST', f'/zones/{zone}/dns_records', json={'type': 'CNAME', 'name': h, 'content': target, 'proxied': True})
                print(t(f'✓ سجل DNS: {h} ← الكمبيوتر', f'✓ DNS record: {h} -> the PC'))
            elif rec['type'] != 'CNAME' or not rec['content'].endswith('.cfargotunnel.com'):
                print(t(f'⚠ يوجد سجل {rec["type"]} آخر للاسم {h} ({rec["content"]}) — احذفه من لوحة Cloudflare ثم أعد setup',
                        f'⚠ Another {rec["type"]} record exists for {h} ({rec["content"]}) - delete it in Cloudflare and run setup again'))
            else:
                print(t(f'  {h} موجود مسبقاً', f'  {h} already exists'))
        print(t(f'\n✅ جاهز: {H} يعمل من هذا الكمبيوتر (خلال دقيقة).', f'\n✅ Ready: {H} is served by this PC (within a minute).'))
        return

    if cmd == 'server-up':
        # حالة النفق في Cloudflare تبقى «سليم» دقائق بعد إطفاء الكمبيوتر (أو إن شغّل النفق جهاز آخر)،
        # لذلك نفتح الموقع فعلياً: يُعتبر الكمبيوتر يعمل فقط إن أجاب نظام الجرس نفسه عبر نفقه.
        if not up(t_main):
            sys.exit(1)
        recs = records()
        if not any(r['content'].startswith(t_main['id']) for r in recs.values()):
            sys.exit(0)   # الموقع موجّه لغير الكمبيوتر حالياً؛ نكتفي بحالة النفق
        sys.exit(0 if all(site_answers(h) for h in hosts) else 1)

    t_sb = tunnel(sb_name)
    names = {f"{t_main['id']}.cfargotunnel.com": t('الكمبيوتر', 'the PC'), f"{t_sb['id']}.cfargotunnel.com": t('الهاتف', 'the PHONE')}
    logs = os.path.join(BASE, 'logs')

    if cmd == 'auto':
        os.makedirs(logs, exist_ok=True)
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(os.path.join(logs, 'watchdog_last.txt'), 'w', encoding='utf-8') as fh:
            fh.write(now)
        recs = records()
        on_phone = any(r['content'].startswith(t_sb['id']) for r in recs.values())
        if not on_phone or up(t_sb) or not up(t_main):
            print(f'{now} OK (on_phone={on_phone}, phone_up={up(t_sb)}, pc_up={up(t_main)})')
            return
        time.sleep(30)
        if up(tunnel(sb_name)):
            print(f'{now} phone tunnel came back')
            return
        target = f"{t_main['id']}.cfargotunnel.com"
        for rec in recs.values():
            if rec['content'] != target:
                cf.call('PATCH', f"/zones/{zone}/dns_records/{rec['id']}", json={'content': target, 'proxied': True})
        with open(os.path.join(logs, 'failover.log'), 'a', encoding='utf-8') as fh:
            fh.write(f'{now} الهاتف منقطع والموقع موجّه له — أُعيد {H} إلى الكمبيوتر تلقائياً\n')
        print(f'{now} ✅ phone offline - moved back to the PC')
        return

    if cmd == 'status':
        st = lambda x: (t('🟢 متصل', '[OK] connected') if up(x) else t('🔴 منقطع', '[X] down'))  # noqa: E731
        print(t(f'\nنفق الكمبيوتر «{main_name}»: {st(t_main)}', f'\nPC tunnel     {main_name}: {st(t_main)}'))
        print(t(f'نفق الهاتف    «{sb_name}»: {st(t_sb)}\n', f'Phone tunnel  {sb_name}: {st(t_sb)}\n'))
        recs = records()
        for h, rec in recs.items():
            print(f'  {h}  {t("←", "->")}  {names.get(rec["content"], rec["content"])}')
        if not recs:
            print(t('  لا يوجد سجل DNS — شغّل: python ops/cf.py setup', '  No DNS record - run: python ops/cf.py setup'))
            return
        on_phone = any(r['content'].startswith(t_sb['id']) for r in recs.values())
        print()
        code = probe(hosts[0])
        if up(t_sb if on_phone else t_main) and code and code < 500:
            print(t('✅ الموقع يعمل.', '✅ The site is up.'))
        elif up(t_sb if on_phone else t_main):
            print(t(f'❌ النفق متصل لكن نظام الجرس نفسه لا يستجيب ({code or "لا رد"}) — متوقف؟ على الكمبيوتر: bell_on.bat',
                    f'❌ The tunnel is connected but the bell system itself does not respond ({code or "no reply"}) - stopped? On the PC: bell_on.bat'))
        elif on_phone and up(t_main):
            print(t('❌ الموقع متوقف: موجّه للهاتف والهاتف منقطع.\n   الحل الآن: switch_to_server.bat (على الكمبيوتر)',
                    '❌ The site is DOWN: it points to the phone and the phone is offline.\n   Fix now: switch_to_server.bat (on the PC)'))
        elif on_phone:
            print(t('❌ الموقع متوقف: الهاتف والكمبيوتر منقطعان.', '❌ The site is DOWN: both the phone and the PC are offline.'))
        else:
            print(t('❌ الموقع متوقف: الكمبيوتر منقطع — تأكد أنه يعمل ومتصل، أو شغّل الطوارئ من الهاتف (BELL 1).',
                    '❌ The site is DOWN: the PC is offline - check it, or start the emergency mode on the phone (BELL 1).'))
        last = os.path.join(logs, 'watchdog_last.txt')
        if os.name == 'nt':
            if os.path.isfile(last):
                age = (time.time() - os.path.getmtime(last)) / 60
                ok = age < 15
                print(t(f'{"✅" if ok else "⚠"} الحارس: آخر فحص قبل {age:.0f} دقيقة' + ('' if ok else ' — لا يعمل! شغّل 3_autostart.bat كمسؤول'),
                        f'{"✅" if ok else "⚠"} Watchdog: last check {age:.0f} min ago' + ('' if ok else ' - NOT running! Run 3_autostart.bat as administrator')))
            else:
                print(t('⚠ الحارس غير مفعّل — شغّل 3_autostart.bat كمسؤول', '⚠ Watchdog not set up - run 3_autostart.bat as administrator'))
        return

    # phone / server
    target_t = t_sb if cmd == 'phone' else t_main
    who = names[f"{target_t['id']}.cfargotunnel.com"]
    ensure_ingress(target_t)
    for i in range(12):
        if up(cf.call('GET', f"/accounts/{acc}/cfd_tunnel/{target_t['id']}")):
            break
        if i == 0:
            print(t(f'… انتظار اتصال نفق {who}', f'... waiting for the tunnel of {who}'))
        time.sleep(5)
    else:
        die(t(f'نفق {who} غير متصل — لم يُحوَّل شيء', f'The tunnel of {who} is not connected - nothing was switched'))
    target = f"{target_t['id']}.cfargotunnel.com"
    recs = records()
    if not recs:
        die(t('لا يوجد سجل DNS — شغّل على الكمبيوتر: python ops/cf.py setup', 'No DNS record - run on the PC: python ops/cf.py setup'))
    for h, rec in recs.items():
        if rec['content'] == target:
            print(t(f'  {h} يشير إلى {who} أصلاً', f'  {h} already points to {who}'))
            continue
        cf.call('PATCH', f"/zones/{zone}/dns_records/{rec['id']}", json={'content': target, 'proxied': True})
        print(f'✓ {h}  {t("←", "->")}  {who}')
    print(t(f'\n✅ {H} يعمل الآن من {who} (خلال دقيقة).', f'\n✅ {H} now runs from {who} (within a minute).'))


if __name__ == '__main__':
    main()
