/* School Bell desktop app (Windows / macOS).
 * - Lives in the tray, starts with the computer, keeps running with no window.
 * - The schedule is downloaded once (first sign-in) and cached on disk; after
 *   that alerts fire from the local clock with no internet.
 * - At each alert: a full-screen, always-on-top window with the text + tone. */
'use strict';
const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, Notification, powerMonitor, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const alerts = require('./www/js/alerts.js');
const { createScheduler } = require('./scheduler');

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
if (process.env.BELL_USERDATA) app.setPath('userData', process.env.BELL_USERDATA);   // tests
if (process.platform === 'win32') app.setAppUserModelId('jo.edu.ras.schoolbell');

if (!app.requestSingleInstanceLock()) { app.quit(); }

const DATA = app.getPath('userData');
const file = (n) => path.join(DATA, n);
const readJSON = (n, d) => { try { return JSON.parse(fs.readFileSync(file(n), 'utf8')); } catch (e) { return d; } };
const writeJSON = (n, v) => { fs.mkdirSync(DATA, { recursive: true }); fs.writeFileSync(file(n), JSON.stringify(v)); };

let config = readJSON('config.json', {});         // { code, server, lang, autostartSet }
let mainWin = null, tray = null, quitting = false;
const alertWins = [];

const scheduler = createScheduler({
  alerts,
  onFire: (ev, schedule) => showAlert(ev, schedule),
  loadFired: () => readJSON('fired.json', []),
  saveFired: (f) => writeJSON('fired.json', f),
});
scheduler.setSchedule(readJSON('schedule.json', null));

const T = (ar, en) => (config.lang === 'en' ? en : ar);

// ── Alert window ──────────────────────────────────────────────────────────
function showAlert(ev, schedule, isTest) {
  closeAlerts();
  const lang = config.lang || 'ar';
  const st = (schedule && schedule.stages && schedule.stages[ev.items[0].stageId]) || null;
  const payload = {
    lang, time: ev.time, test: !!isTest,
    lines: ev.items.map((it) => (lang === 'en' ? it.en || it.ar : it.ar)),
    targets: schedule && schedule.who && schedule.who.type === 'teacher'
      ? ev.items.map((it) => it.targets.map((t) => (lang === 'en' ? t.en || t.ar : t.ar)).join('، ')).join(' | ') : '',
    itemTargets: schedule && schedule.who && schedule.who.type === 'teacher' && ev.items.length > 1
      ? ev.items.map((it) => it.targets.map((t) => (lang === 'en' ? t.en || t.ar : t.ar)).join('، ')) : null,
    logo: st && st.logo && st.logo.startsWith('data:') ? st.logo : null,
    background: st && st.background && st.background.startsWith('data:') ? st.background : null,
    tone: [st && st.tone, schedule && schedule.tone].find((u) => u && u.startsWith('data:')) || null,
  };
  fs.writeFileSync(file('current-alert.json'), JSON.stringify(payload));
  // One window per screen, so it's seen whichever screen faces the class.
  screen.getAllDisplays().forEach((d, i) => {
    const w = new BrowserWindow({
      x: d.bounds.x, y: d.bounds.y, width: d.bounds.width, height: d.bounds.height,
      // Frameless and screen-sized rather than `fullscreen`: fullscreen resets the
      // always-on-top level on some systems, and the alert must cover everything.
      frame: false, alwaysOnTop: true, skipTaskbar: true, resizable: false, movable: false, show: false,
      backgroundColor: '#12305e',
      webPreferences: { preload: path.join(__dirname, 'preload.js'), backgroundThrottling: false },
    });
    w.setAlwaysOnTop(true, 'screen-saver');
    w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    w.loadFile(path.join(__dirname, 'www', 'alert.html'), { query: { sound: i === 0 ? '1' : '0' } });
    w.once('ready-to-show', () => {
      w.show();
      w.setAlwaysOnTop(true, 'screen-saver');
      w.setBounds(d.bounds);
      w.moveTop();
      w.focus();
    });
    w.on('closed', () => { const k = alertWins.indexOf(w); if (k >= 0) alertWins.splice(k, 1); });
    alertWins.push(w);
  });
  if (Notification.isSupported()) {
    new Notification({ title: payload.lines.join(' | '), body: payload.targets || '', silent: true }).show();
  }
  setTimeout(closeAlerts, 60 * 1000);
}

function closeAlerts() { alertWins.slice().forEach((w) => { if (!w.isDestroyed()) w.close(); }); }

// ── Main window (sign-in + live screen) ───────────────────────────────────
function createMainWindow(show) {
  mainWin = new BrowserWindow({
    width: 1100, height: 720, show, backgroundColor: '#12305e', autoHideMenuBar: true,
    title: 'جرس الحصص',
    icon: path.join(__dirname, 'www', 'icons', 'icon-512.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.js'), backgroundThrottling: false },
  });
  mainWin.loadFile(path.join(__dirname, 'www', 'index.html'));
  mainWin.on('close', (e) => {       // closing only hides: alerts keep running
    if (!quitting) { e.preventDefault(); mainWin.hide(); }
  });
}

function openMain() { if (!mainWin) createMainWindow(true); mainWin.show(); mainWin.focus(); }

function buildTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, 'www', 'icons', 'icon-192.png')).resize({ width: 18, height: 18 });
  tray = new Tray(icon);
  tray.setToolTip('جرس الحصص');
  tray.on('click', openMain);
  refreshTray();
}

function refreshTray() {
  if (!tray) return;
  const nx = scheduler.next();
  const nextLabel = nx
    ? `${T('التنبيه القادم', 'Next alert')}: ${nx.day === 0 ? '' : `(+${nx.day}) `}${nx.ev.time}`
    : T('لا يوجد جدول بعد — سجّل الدخول', 'No schedule yet — sign in');
  const login = app.getLoginItemSettings();
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: nextLabel, enabled: false },
    { type: 'separator' },
    { label: T('فتح الشاشة', 'Open screen'), click: openMain },
    { label: T('تجربة التنبيه', 'Test alert'), click: testAlert },
    { label: T('تشغيل مع بدء الجهاز', 'Start with the computer'), type: 'checkbox', checked: login.openAtLogin,
      click: (mi) => app.setLoginItemSettings({ openAtLogin: mi.checked, args: ['--hidden'] }) },
    { type: 'separator' },
    { label: T('إنهاء (تتوقف التنبيهات)', 'Quit (alerts stop)'), click: () => { quitting = true; app.quit(); } },
  ]));
}

function testAlert() {
  const s = scheduler.schedule;
  const nx = scheduler.next();
  const now = new Date();
  const hm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  const ev = nx ? Object.assign({}, nx.ev, { time: hm })
    : { time: hm, items: [{ ar: 'تنبيه تجريبي', en: 'Test alert', stageId: null, targets: [] }] };
  showAlert(ev, s, true);
}

// ── IPC from the renderer (bridge-desktop.js) ─────────────────────────────
ipcMain.handle('bell:getCode', () => config.code || '');
ipcMain.handle('bell:saveLogin', (e, code, server) => {
  config = Object.assign(config, { code, server }); writeJSON('config.json', config); return true;
});
ipcMain.handle('bell:logout', () => {
  config.code = ''; writeJSON('config.json', config);
  scheduler.setSchedule(null); writeJSON('schedule.json', null); refreshTray(); return true;
});
ipcMain.handle('bell:setSchedule', (e, schedule, lang) => {
  config.lang = lang; writeJSON('config.json', config);
  writeJSON('schedule.json', schedule);
  scheduler.setSchedule(schedule);
  refreshTray();
  const n = alerts.weeklyPlan(schedule, lang).length;
  return T(`${n} تنبيه أسبوعي — يعمل بدون إنترنت والتطبيق في شريط المهام`,
           `${n} weekly alerts — work offline while the app is in the tray`);
});
ipcMain.handle('bell:test', () => { testAlert(); return true; });
ipcMain.handle('bell:alertPayload', () => readJSON('current-alert.json', null));
ipcMain.handle('bell:closeAlert', () => { closeAlerts(); return true; });

// ── Lifecycle ─────────────────────────────────────────────────────────────
app.on('second-instance', openMain);
app.whenReady().then(() => {
  if (!config.autostartSet) {       // first run: start with the computer by default
    app.setLoginItemSettings({ openAtLogin: true, args: ['--hidden'] });
    config.autostartSet = true; writeJSON('config.json', config);
  }
  const hidden = process.argv.includes('--hidden') && !!config.code;
  createMainWindow(!hidden);
  buildTray();
  setInterval(() => { scheduler.tick(); }, 1000);
  setInterval(refreshTray, 60 * 1000);
  powerMonitor.on('resume', () => scheduler.tick());
  if (process.env.BELL_SELFTEST) {
    require(process.env.BELL_SELFTEST)(app, { getMain: () => mainWin, getAlerts: () => alertWins, scheduler });
  }
});
app.on('window-all-closed', (e) => { e.preventDefault(); });   // stay in the tray
app.on('before-quit', () => { quitting = true; });
