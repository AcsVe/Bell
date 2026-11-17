/* تنبيه الإدارة بصدور تحديث جديد للنظام.
   يسأل الخادم عن رقم النسخة كل 24 ساعة (وعند العودة إلى الصفحة إن مضى يوم)؛ فإن تغيّر عن رقم الصفحة المفتوحة
   ظهر شريط يدعو إلى تحديث الصفحة. لا يقاطع العمل الجاري ولا يحدّث الصفحة تلقائياً. */
(function () {
  var s = document.currentScript;
  if (!s) return;
  var current = s.getAttribute('data-version');
  var url = s.getAttribute('data-url') || '/api/app-version';
  if (!current) return;
  var DAY = 24 * 60 * 60 * 1000;
  var last = Date.now();
  var bar = null;
  var en = (document.documentElement.lang || '').toLowerCase().indexOf('en') === 0;
  var T = en
    ? { msg: 'A new version of the system is available. Refresh the page to see the latest changes.', now: 'Refresh now', close: 'Close' }
    : { msg: 'صدر تحديث جديد للنظام. حدّث الصفحة لتظهر آخر المستجدات.', now: 'تحديث الآن', close: 'إغلاق' };

  function show() {
    if (bar) return;
    bar = document.createElement('div');
    bar.setAttribute('role', 'status');
    bar.setAttribute('data-update-notice', '1');
    bar.dir = document.documentElement.dir || (en ? 'ltr' : 'rtl');
    var st = bar.style;
    st.position = 'fixed'; st.left = '16px'; st.right = '16px'; st.bottom = '16px'; st.margin = '0 auto'; st.maxWidth = '640px';
    st.zIndex = '2147483000'; st.display = 'flex'; st.flexWrap = 'wrap'; st.alignItems = 'center'; st.gap = '10px 14px';
    st.padding = '12px 16px'; st.borderRadius = '12px'; st.background = '#0f172a'; st.color = '#ffffff';
    st.fontSize = '14px'; st.lineHeight = '1.6'; st.boxShadow = '0 10px 30px rgba(0,0,0,.35)';
    var msg = document.createElement('span');
    msg.textContent = T.msg; msg.style.flex = '1 1 240px';
    var go = document.createElement('button');
    go.type = 'button'; go.textContent = T.now;
    go.style.cssText = 'border:0;border-radius:8px;padding:6px 14px;background:#fff;color:#0f172a;font:inherit;font-weight:700;cursor:pointer';
    go.addEventListener('click', function () { location.reload(); });
    var x = document.createElement('button');
    x.type = 'button'; x.textContent = '×'; x.setAttribute('aria-label', T.close);
    x.style.cssText = 'border:0;background:transparent;color:#fff;font-size:20px;line-height:1;cursor:pointer;padding:0 4px';
    x.addEventListener('click', function () { bar.remove(); bar = null; last = Date.now(); });
    bar.appendChild(msg); bar.appendChild(go); bar.appendChild(x);
    document.body.appendChild(bar);
  }

  function check() {
    last = Date.now();
    fetch(url, { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.version && d.version !== current) show(); })
      .catch(function () {});
  }

  setInterval(function () { if (!bar) check(); }, DAY);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && !bar && Date.now() - last >= DAY) check();
  });
})();
