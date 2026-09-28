/* Display screen: cached schedule → local alerts. Works fully offline once the
 * schedule has been downloaded; syncs silently whenever the network is up. */
(function () {
  'use strict';
  const A = window.BellAlerts, S = window.BellStore;
  /* Inside the Android/iOS/desktop apps a native bridge is present. It provides
   * the server address and stored code, and registers the alerts with the
   * operating system so they fire in the background with no network. */
  const N = window.BellNative || null;
  const abs = (u) => (u && N && u.startsWith('/') ? N.apiBase + u : u);
  const $ = (id) => document.getElementById(id);

  const I18N = {
    ar: {
      gateTitle: 'جرس الحصص جاهز', gateText: 'اضغط الزر لبدء العمل وتفعيل الصوت. اترك الشاشة مفتوحة.',
      gateBtn: 'ابدأ', needNetTitle: 'يلزم اتصال بالإنترنت', retry: 'إعادة المحاولة',
      needNetText: 'يحتاج الجهاز للاتصال مرة واحدة لتنزيل جدول الحصص، ثم يعمل بدون إنترنت.',
      tapToClose: 'اضغط لإغلاق التنبيه', testAlert: 'تجربة التنبيه', install: 'تثبيت التطبيق',
      fullscreen: 'ملء الشاشة', syncNow: 'تحديث الجدول الآن', logout: 'تسجيل الخروج من هذا الجهاز', close: 'إغلاق',
      online: 'متصل', offline: 'بدون إنترنت — التنبيهات تعمل', langBtn: 'English',
      now: 'الآن', endsAt: 'تنتهي', remaining: 'متبقٍ', min: 'د', nextAlert: 'التنبيه القادم',
      noSchool: 'لا دوام اليوم', beforeDay: 'يبدأ الدوام الساعة', afterDay: 'انتهى الدوام لهذا اليوم',
      codeInvalid: 'لم يعد كود الدخول صالحاً. التنبيهات مستمرة بالجدول المحفوظ.', relogin: 'أدخل الكود الجديد',
      lastSync: 'آخر تحديث للجدول:', never: 'لم يُحدَّث بعد', confirmLogout: 'سيتوقف هذا الجهاز عن التنبيه. متابعة؟',
      teacher: 'معلم', sectionCount: 'شعبة', testText: 'هذا تنبيه تجريبي',
      logoutOffline: 'يلزم اتصال بالإنترنت لتسجيل الخروج.',
    },
    en: {
      gateTitle: 'School bell is ready', gateText: 'Tap the button to start and enable sound. Keep this screen open.',
      gateBtn: 'Start', needNetTitle: 'Internet connection needed', retry: 'Try again',
      needNetText: 'The device needs to connect once to download the schedule, then it works offline.',
      tapToClose: 'Tap to dismiss', testAlert: 'Test alert', install: 'Install app',
      fullscreen: 'Full screen', syncNow: 'Update schedule now', logout: 'Sign this device out', close: 'Close',
      online: 'Online', offline: 'Offline — alerts still work', langBtn: 'العربية',
      now: 'Now', endsAt: 'ends', remaining: 'left', min: 'min', nextAlert: 'Next alert',
      noSchool: 'No school today', beforeDay: 'School starts at', afterDay: 'The school day has ended',
      codeInvalid: 'This sign-in code is no longer valid. Alerts continue from the saved schedule.', relogin: 'Enter the new code',
      lastSync: 'Schedule last updated:', never: 'not yet', confirmLogout: 'This device will stop alerting. Continue?',
      teacher: 'Teacher', sectionCount: 'sections', testText: 'This is a test alert',
      logoutOffline: 'An internet connection is needed to sign out.',
    },
  };
  const SYNC_EVERY_MS = 5 * 60 * 1000;
  const FIRE_WINDOW_SEC = 90;
  const ALERT_SHOW_MS = 60 * 1000;

  let lang = localStorage.getItem('bell:lang') || document.body.dataset.lang || 'ar';
  let schedule = null, lastSync = null, codeInvalid = false, online = navigator.onLine;
  let todayKey = '', todayEvents = [], alertTimer = null, wakeLock = null, installPrompt = null;
  const fired = new Set(JSON.parse(sessionStorage.getItem('bell:fired') || '[]'));
  const t = (k) => (I18N[lang] || I18N.ar)[k] || k;
  const pick = (o) => (o ? (lang === 'en' ? o.en || o.ar : o.ar) : '');
  const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ph = (o) => esc(pick(o));   // for innerHTML

  function deviceId() {
    let id = localStorage.getItem('bell:device');
    if (!id) {
      id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
      localStorage.setItem('bell:device', id);
    }
    return id;
  }

  // ── Language ───────────────────────────────────────────────────────────
  function applyLang() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === 'en' ? 'ltr' : 'rtl';
    document.querySelectorAll('[data-t]').forEach((el) => { el.textContent = t(el.dataset.t); });
    $('langBtn').textContent = t('langBtn');
    renderHeader(); renderIdle(new Date()); renderStatus();
  }

  // ── Sync ───────────────────────────────────────────────────────────────
  async function sync() {
    const q = schedule ? `?v=${schedule.version}&k=${encodeURIComponent(schedule.key || '')}` : '';
    let res;
    try {
      const headers = { 'X-Device-Id': deviceId() };
      if (N) headers['X-Bell-Code'] = await N.getCode();
      res = await fetch(abs('/api/schedule' + q), { credentials: N ? 'omit' : 'same-origin',
                                                    cache: 'no-store', headers });
    } catch (e) {
      online = false; renderStatus();
      return false;
    }
    online = true;
    if (res.status === 401) {
      if (!schedule) { goLogin(); return false; }
      codeInvalid = true; renderStatus(); return false;
    }
    if (!res.ok) { renderStatus(); return false; }
    const data = await res.json();
    codeInvalid = false;
    lastSync = Date.now();
    await S.set('lastSync', lastSync);
    if (!data.unchanged) {
      if (N) await inlineAssets(data);
      schedule = data;
      await S.set('schedule', data);
      registerNative();
      todayKey = '';               // force events to be recomputed
      precacheAssets();
      renderHeader();
    }
    renderStatus();
    return true;
  }

  function goLogin() { if (N) N.goLogin(); else location.href = '/login'; }

  /* Native apps have no service worker: embed logos/backgrounds as data URLs
   * so they still show offline. The tone plays from the OS notification. */
  async function inlineAssets(data) {
    const toData = async (u) => {
      try {
        const r = await fetch(abs(u)); if (!r.ok) return u;
        const b = await r.blob();
        return await new Promise((ok) => { const f = new FileReader(); f.onload = () => ok(f.result); f.readAsDataURL(b); });
      } catch (e) { return u; }
    };
    for (const st of Object.values(data.stages || {})) {
      if (st.logo) st.logo = await toData(st.logo);
      if (st.background) st.background = await toData(st.background);
    }
    data.tone = await toData(data.tone);
  }

  let nativeStatus = '';
  async function registerNative() {
    if (!N || !schedule) return;
    try { nativeStatus = (await N.onSchedule(schedule, lang)) || ''; }
    catch (e) { nativeStatus = String(e && e.message || e); }
    renderStatus();
  }

  function precacheAssets() {
    if (!schedule || !navigator.serviceWorker || !navigator.serviceWorker.controller) return;
    const urls = [schedule.tone];
    Object.values(schedule.stages || {}).forEach((s) => { if (s.logo) urls.push(s.logo); if (s.background) urls.push(s.background); });
    navigator.serviceWorker.controller.postMessage({ type: 'precache', urls: urls.filter(Boolean) });
  }

  // ── Rendering ──────────────────────────────────────────────────────────
  function stages() { return Object.values((schedule && schedule.stages) || {}); }
  function mainStage() { return stages()[0] || null; }

  function renderHeader() {
    if (!schedule) return;
    const st = mainStage();
    const multi = stages().length > 1;
    $('stageName').textContent = st ? (multi ? stages().map(pick).join(' · ') : pick(st)) : '';
    const who = schedule.who;
    $('whoName').textContent = who.type === 'teacher'
      ? `${t('teacher')}: ${pick(who)} · ${schedule.targets.length} ${t('sectionCount')}` : pick(who);
    const logo = $('logo');
    if (st && st.logo) { logo.src = abs(st.logo); logo.hidden = false; } else logo.hidden = true;
    const bg = $('bg');
    if (st && st.background && !multi) {
      bg.style.backgroundImage = `url("${abs(st.background)}")`; bg.classList.add('has-img');
    } else { bg.style.backgroundImage = ''; bg.classList.remove('has-img'); }
    $('tone').src = abs(schedule.tone);
  }

  function renderStatus() {
    const el = $('status');
    el.classList.toggle('offline', !online);
    el.querySelector('span').textContent = online ? t('online') : t('offline');
    const b = $('banner');
    if (codeInvalid) {
      b.innerHTML = `<span>${t('codeInvalid')}</span><a href="/login">${t('relogin')}</a>`;
      b.hidden = false;
    } else b.hidden = true;
    $('syncInfo').textContent = `${t('lastSync')} ${lastSync ? new Date(lastSync).toLocaleString(lang === 'en' ? 'en-GB' : 'ar-JO') : t('never')}`
      + (nativeStatus ? ` · ${nativeStatus}` : '');
  }

  const hm = (d) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;

  function renderIdle(now) {
    $('hm').textContent = hm(now);
    $('sec').textContent = String(now.getSeconds()).padStart(2, '0');
    $('date').textContent = now.toLocaleDateString(lang === 'en' ? 'en-GB' : 'ar-JO-u-nu-latn',
      { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    if (!schedule) { $('now').innerHTML = ''; $('next').innerHTML = ''; return; }

    const status = A.currentStatus(schedule, now);
    const nowSec = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
    const html = [];
    const schoolDay = status.some((s) => s.schoolDay);
    // Group targets that are in the same period with the same end time.
    const groups = new Map();
    status.forEach((s) => {
      if (!s.current) return;
      const k = s.current.ar + '|' + s.current.end;
      if (!groups.has(k)) groups.set(k, { p: s.current, names: [] });
      groups.get(k).names.push(ph(s.target));
    });
    if (!schoolDay) {
      html.push(`<div class="label">${t('noSchool')}</div>`);
    } else if (groups.size === 1 && status.length === 1 || groups.size === 1 && [...groups.values()][0].names.length === status.length) {
      const { p } = [...groups.values()][0];
      const s = A.toMin(p.start) * 60, e = A.toMin(p.end) * 60;
      const left = Math.max(0, Math.ceil((e - nowSec) / 60));
      const pct = Math.min(100, Math.max(0, ((nowSec - s) / (e - s)) * 100));
      html.push(`<div class="label">${t('now')}: ${ph(p)}</div>
        <div class="sub"><span dir="ltr">${p.start} – ${p.end}</span> · ${left} ${t('min')} ${t('remaining')}</div>
        <div class="progress"><span style="width:${pct.toFixed(1)}%"></span></div>`);
    } else if (groups.size) {
      groups.forEach(({ p, names }) => {
        html.push(`<div class="row"><span>${names.join('، ')}</span><span><b>${ph(p)}</b> · ${t('endsAt')} <span dir="ltr">${p.end}</span></span></div>`);
      });
    } else {
      const starts = status.filter((s) => s.dayStart).map((s) => s.dayStart).sort();
      const ends = status.filter((s) => s.dayEnd).map((s) => s.dayEnd).sort();
      if (starts.length && hm(now) < starts[0]) html.push(`<div class="label">${t('beforeDay')} <span dir="ltr">${starts[0]}</span></div>`);
      else if (ends.length && hm(now) >= ends[ends.length - 1]) html.push(`<div class="label">${t('afterDay')}</div>`);
    }
    $('now').innerHTML = html.join('');

    const nx = A.nextEvent(todayEvents, now);
    $('next').innerHTML = nx
      ? `<span class="k">${t('nextAlert')}:</span> <b dir="ltr">${nx.time}</b> — ${nx.items.map(ph).join(' | ')}` : '';
  }

  // ── Alerts ─────────────────────────────────────────────────────────────
  function playTone() {
    if (N && N.nativeSound) return;   // the OS notification already plays the tone
    const a = $('tone');
    try { a.currentTime = 0; const p = a.play(); if (p && p.catch) p.catch(() => showGate()); } catch (e) { /* ignore */ }
  }

  function fire(ev, isTest) {
    const item = ev.items[0];
    const st = (schedule && schedule.stages && schedule.stages[item.stageId]) || mainStage();
    const box = $('alert');
    if (st && st.background) { box.style.backgroundImage = `url("${abs(st.background)}")`; box.classList.add('has-img'); }
    else { box.style.backgroundImage = ''; box.classList.remove('has-img'); }
    const logo = $('alertLogo');
    if (st && st.logo) { logo.src = abs(st.logo); logo.hidden = false; } else logo.hidden = true;
    $('alertTime').textContent = ev.time;
    $('alertText').innerHTML = ev.items.map((it) => {
      const parts = ph(it).split(/ [-—] /);
      return parts.length === 2 ? `<span>${parts[0]}</span><span class="l2">${parts[1]}</span>` : `<span>${parts[0]}</span>`;
    }).join('<span class="sep"></span>');
    const multiTargets = schedule && schedule.who.type === 'teacher';
    $('alertTargets').textContent = isTest ? t('testText')
      : multiTargets ? ev.items.map((it) => it.targets.map(pick).join('، ')).join(' | ') : '';
    box.hidden = false;
    playTone();
    clearTimeout(alertTimer);
    alertTimer = setTimeout(() => { box.hidden = true; }, ALERT_SHOW_MS);
  }

  function dayKey(d) { return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`; }

  function tick() {
    const now = new Date();
    const key = dayKey(now);
    if (key !== todayKey) {
      todayKey = key;
      todayEvents = A.eventsForDate(schedule, now);
      [...fired].forEach((f) => { if (!f.startsWith(key + '@')) fired.delete(f); });
    }
    A.dueEvents(todayEvents, now, FIRE_WINDOW_SEC).forEach((ev) => {
      const id = `${key}@${ev.min}`;
      if (fired.has(id)) return;
      fired.add(id);
      sessionStorage.setItem('bell:fired', JSON.stringify([...fired]));
      if (!(N && N.inAppAlerts === false)) fire(ev, false);
    });
    renderIdle(now);
  }

  // ── Audio unlock / wake lock ───────────────────────────────────────────
  function showGate() { $('gate').hidden = false; }

  async function unlock() {
    const a = $('tone');
    a.muted = true;
    try { await a.play(); a.pause(); a.currentTime = 0; } catch (e) { /* ignore */ }
    a.muted = false;
    $('gate').hidden = true;
    requestWakeLock();
  }

  async function requestWakeLock() {
    try { if ('wakeLock' in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } } catch (e) { /* not supported / not allowed */ }
  }

  // ── Menu ───────────────────────────────────────────────────────────────
  async function onMenu(act) {
    if (act === 'close') { $('menu').hidden = true; return; }
    if (act === 'lang') { lang = lang === 'en' ? 'ar' : 'en'; localStorage.setItem('bell:lang', lang); applyLang(); registerNative(); }
    if (act === 'test') {
      $('menu').hidden = true;
      const now = new Date();
      const nx = A.nextEvent(todayEvents, now) || todayEvents[0];
      const ev = nx ? Object.assign({}, nx, { time: hm(now) }) : { time: hm(now), items: [{ ar: t('testText'), en: t('testText'), stageId: null, targets: [] }] };
      if (N && N.test) N.test(ev.items.map(pick).join(' | '), t('testText')).catch(() => {});
      if (!(N && N.inAppAlerts === false)) fire(ev, true);
      return;
    }
    if (act === 'install' && installPrompt) { installPrompt.prompt(); installPrompt = null; $('installBtn').hidden = true; }
    if (act === 'fullscreen') { try { await document.documentElement.requestFullscreen(); } catch (e) { /* ignore */ } }
    if (act === 'sync') { await sync(); }
    if (act === 'logout') {
      if (!confirm(t('confirmLogout'))) return;
      if (N) {   // native: forget the code and cancel OS alerts (works offline)
        await S.del('schedule'); await S.del('lastSync');
        await N.logout(); N.goLogin(); return;
      }
      if (!navigator.onLine) { alert(t('logoutOffline')); return; }
      await S.del('schedule'); await S.del('lastSync');
      const f = document.createElement('form'); f.method = 'post'; f.action = '/logout';
      document.body.appendChild(f); f.submit();
      return;
    }
    $('menu').hidden = true;
  }

  // ── Boot ───────────────────────────────────────────────────────────────
  async function boot() {
    if ('serviceWorker' in navigator && !N) navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installPrompt = e; $('installBtn').hidden = false; });

    $('menuBtn').onclick = () => { renderStatus(); $('menu').hidden = false; };
    $('menu').onclick = (e) => { const b = e.target.closest('button'); if (b) onMenu(b.dataset.act); else if (e.target.id === 'menu') $('menu').hidden = true; };
    $('alert').onclick = () => { $('alert').hidden = true; };
    $('gateBtn').onclick = unlock;
    $('retryBtn').onclick = async () => { if (await sync()) { $('empty').hidden = true; start(); } };
    window.addEventListener('online', () => { online = true; renderStatus(); sync(); });
    window.addEventListener('offline', () => { online = false; renderStatus(); });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') { tick(); requestWakeLock(); if (navigator.onLine) sync(); }
    });

    schedule = await S.get('schedule');
    lastSync = await S.get('lastSync');
    applyLang();
    if (schedule) { start(); registerNative(); }   // re-assert OS alerts on every launch
    const ok = await sync();
    if (!started) {
      if (schedule) start();          // first run: schedule just downloaded
      else if (!ok) $('empty').hidden = false;
    }
    setInterval(() => { if (navigator.onLine) sync(); }, SYNC_EVERY_MS);
  }

  let started = false;
  function start() {
    if (started) return;
    started = true;
    renderHeader();
    todayKey = '';
    tick();
    setInterval(tick, 1000);
    // Browsers block sound until the first tap on the page.
    const ctx = window.AudioContext ? new AudioContext() : null;
    if (N && (N.nativeSound || N.inAppAlerts === false)) requestWakeLock();   // no in-page sound needed
    else if (!ctx || ctx.state !== 'running') showGate(); else requestWakeLock();
    if (ctx) ctx.close();
  }

  boot();
})();
