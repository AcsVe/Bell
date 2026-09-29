/* Drives native/web/bridge-mobile.js with fake Capacitor plugins to check what
 * gets registered with Android (native BellAlarm) and iOS (LocalNotifications). */
const assert = require('assert');
const A = require('../static/js/alerts.js');
const { createBridge, capWeekday } = require('../native/web/bridge-mobile.js');

function memLS() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
           removeItem: (k) => m.delete(k) };
}
const P = (n, kind, ar, en, start, end) => ({ n, kind, ar, en, start, end });
const day = [
  P(1, 'class', 'الحصة الأولى', 'Period 1', '08:00', '08:45'),
  P(2, 'class', 'الحصة الثانية', 'Period 2', '08:45', '09:30'),
  P(3, 'break', 'الاستراحة', 'Break', '09:30', '09:50'),
  P(4, 'class', 'الحصة الثالثة', 'Period 3', '09:50', '10:35'),
];
const schedule = {
  version: 3, who: { type: 'section', ar: 'الصف الأول - أ', en: 'Grade 1 - A' },
  targets: [{ sectionId: 1, stageId: 1, gradeId: 10, ar: 'الصف الأول - أ', en: 'Grade 1 - A' }],
  days: { 10: { 6: day, 0: day, 1: day, 2: day, 3: day } },   // Sun..Thu
  stages: {}, tone: '/static/sounds/chime.wav',
};

(async () => {
  assert.strictEqual(capWeekday(6), 1);   // Sunday
  assert.strictEqual(capWeekday(0), 2);   // Monday
  assert.strictEqual(capWeekday(5), 7);   // Saturday

  // ── Android ──
  const calls = [];
  let stored = [];
  const android = {
    getPlatform: () => 'android',
    Plugins: {
      LocalNotifications: {
        checkPermissions: async () => ({ display: 'granted' }),
        requestPermissions: async () => ({ display: 'granted' }),
        getPending: async () => ({ notifications: [] }),
      },
      BellAlarm: {
        status: async () => ({ count: stored.length, exact: true, notifications: true, battery: false }),
        setAlarms: async ({ alarms }) => { calls.push(['setAlarms', alarms.length]); stored = alarms; return {}; },
        clear: async () => { calls.push(['clear']); stored = []; },
        openExactAlarmSettings: async () => calls.push(['exactSettings']),
        openBatterySettings: async () => calls.push(['battery']),
      },
    },
  };
  const ls = memLS();
  const b = createBridge(android, A, ls, { defaultServer: 'https://x.example' });
  assert.strictEqual(b.apiBase, 'https://x.example');
  let msg = await b.onSchedule(schedule, 'ar');
  assert.strictEqual(stored.length, 5 * 5);                 // 5 days × 5 alerts
  const sun845 = stored.find((a) => a.weekday === 1 && a.hour === 8 && a.minute === 45);
  assert.strictEqual(sun845.title, 'انتهت الحصة الأولى - بداية الحصة الثانية');
  assert.strictEqual(sun845.body, 'الصف الأول - أ');
  assert.ok(stored.some((a) => a.weekday === 5 && a.hour === 10 && a.minute === 35 && a.title === 'انتهت الحصة الثالثة'));
  assert.ok(!stored.some((a) => a.weekday === 6 || a.weekday === 7), 'no Friday/Saturday alarms');
  assert.ok(msg.includes('25'));
  // unchanged schedule → no re-registration
  await b.onSchedule(schedule, 'ar');
  assert.strictEqual(calls.filter((c) => c[0] === 'setAlarms').length, 1);
  // language change → re-registered with English text
  await b.onSchedule(schedule, 'en');
  assert.strictEqual(calls.filter((c) => c[0] === 'setAlarms').length, 2);
  assert.ok(stored[0].title.startsWith('Start of') || stored[0].title.startsWith('End of'));
  // setup lists the battery step with an action that opens settings
  const issues = await b.setup('ar');
  assert.deepStrictEqual(issues.map((i) => i.key), ['battery']);
  await issues[0].action();
  assert.ok(calls.some((c) => c[0] === 'battery'));
  // login/logout
  await b.saveLogin('ABC123', 'https://school.example');
  assert.strictEqual(await b.getCode(), 'ABC123');
  assert.strictEqual(b.apiBase, 'https://school.example');
  await b.logout();
  assert.strictEqual(await b.getCode(), '');
  assert.strictEqual(stored.length, 0);

  // ── iOS ──
  let pending = [];
  const iosCalls = [];
  const ios = {
    getPlatform: () => 'ios',
    Plugins: {
      LocalNotifications: {
        checkPermissions: async () => ({ display: 'granted' }),
        requestPermissions: async () => ({ display: 'granted' }),
        getPending: async () => ({ notifications: pending }),
        cancel: async ({ notifications }) => { iosCalls.push(['cancel', notifications.length]); pending = []; },
        schedule: async ({ notifications }) => { iosCalls.push(['schedule', notifications.length]); pending = pending.concat(notifications); },
      },
    },
  };
  const il = memLS();
  const ib = createBridge(ios, A, il, { defaultServer: 'https://x.example' });
  msg = await ib.onSchedule(schedule, 'ar');
  assert.strictEqual(pending.length, 25);
  const n = pending.find((x) => x.schedule.on && x.schedule.on.weekday === 1 && x.schedule.on.hour === 8 && x.schedule.on.minute === 45);
  assert.ok(n && n.sound === 'chime.wav' && n.schedule.on.second === 0);
  await ib.onSchedule(schedule, 'ar');
  assert.strictEqual(iosCalls.filter((c) => c[0] === 'schedule').length, 1, 'unchanged → not re-registered');

  // iOS overflow: a teacher across stages with > 60 distinct weekly times → rolling one-shots
  const busy = JSON.parse(JSON.stringify(schedule));
  busy.who = { type: 'teacher', ar: 'م', en: 'T' };
  const shifted = day.map((p) => Object.assign({}, p, { start: p.start.replace(/:(\d)(\d)$/, (m, a, b2) => `:${a}${(+b2 + 3) % 10}`),
                                                      end: p.end.replace(/:(\d)(\d)$/, (m, a, b2) => `:${a}${(+b2 + 3) % 10}`) }));
  const shifted2 = day.map((p) => Object.assign({}, p, { start: p.start.replace(/:(\d)(\d)$/, (m, a, b2) => `:${a}${(+b2 + 6) % 10}`),
                                                       end: p.end.replace(/:(\d)(\d)$/, (m, a, b2) => `:${a}${(+b2 + 6) % 10}`) }));
  busy.targets.push({ sectionId: 2, stageId: 2, gradeId: 20, ar: 'ب', en: 'B' }, { sectionId: 3, stageId: 3, gradeId: 30, ar: 'ج', en: 'C' });
  busy.days[20] = { 6: shifted, 0: shifted, 1: shifted, 2: shifted, 3: shifted };
  busy.days[30] = { 6: shifted2, 0: shifted2, 1: shifted2, 2: shifted2, 3: shifted2 };
  const built = ib._buildRequests(busy, 'ar', new Date(2026, 8, 27, 7, 0));
  assert.strictEqual(built.mode, 'rolling');
  assert.strictEqual(built.requests.length, 60);
  assert.ok(built.requests.every((r) => r.schedule.at instanceof Date));

  // Per-stage tones: stage 2 has its own WAV, stage 3 an MP3, stage 1 the default.
  const { toneFile } = require('../native/web/bridge-mobile.js');
  const WAV = 'data:audio/wav;base64,UklGRgAAAAA=', MP3 = 'data:audio/mpeg;base64,SUQzAAAA';
  const toned = JSON.parse(JSON.stringify(busy));
  toned.stages = { 1: { id: 1 }, 2: { id: 2, tone: WAV }, 3: { id: 3, tone: MP3 } };
  const wavName = toneFile(WAV).name, mp3Name = toneFile(MP3).name;
  assert.ok(/^tone-\w+\.wav$/.test(wavName) && /\.mp3$/.test(mp3Name));
  const bi = ib._buildRequests(toned, 'ar', new Date(2026, 8, 27, 7, 0));
  const sounds = new Set(bi.requests.map((r) => r.sound));
  assert.deepStrictEqual([...sounds].sort(), ['chime.wav', wavName].sort(), 'iOS: WAV used, MP3 falls back to chime');
  assert.deepStrictEqual(bi.sounds.map((f) => f.name), [wavName]);
  const ab = createBridge(android, A, memLS(), { defaultServer: 'https://x.example' });
  const ba = ab._buildRequests(toned, 'ar', new Date(2026, 8, 27, 7, 0));
  assert.deepStrictEqual([...new Set(ba.alarms.map((a) => a.sound))].sort(), ['', mp3Name, wavName].sort());
  assert.strictEqual(ba.sounds.length, 2);
  // school tone uploaded (toneCustom) → stages without their own use it
  toned.toneCustom = true; toned.tone = WAV;
  const bc = ab._buildRequests(toned, 'ar', new Date(2026, 8, 27, 7, 0));
  assert.ok(!bc.alarms.some((a) => a.sound === ''), 'no alarm left on the bundled chime');
  assert.notStrictEqual(bc.signature, ba.signature);

  console.log('bridge-mobile.js: all tests passed');
})().catch((e) => { console.error(e); process.exit(1); });
