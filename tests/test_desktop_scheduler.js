const assert = require('assert');
const alerts = require('../static/js/alerts.js');
const { createScheduler } = require('../desktop/scheduler.js');

const P = (n, kind, ar, en, start, end) => ({ n, kind, ar, en, start, end });
const day = [
  P(1, 'class', 'الحصة الأولى', 'Period 1', '08:00', '08:45'),
  P(2, 'class', 'الحصة الثانية', 'Period 2', '08:45', '09:30'),
];
const schedule = { who: { type: 'section', ar: 'أ', en: 'A' },
  targets: [{ sectionId: 1, stageId: 1, gradeId: 10, ar: 'أ', en: 'A' }], days: { 10: { 6: day, 0: day } } };

let clock = new Date(2026, 8, 27, 8, 44, 58);   // Sunday
const fired = [];
let saved = [];
const s = createScheduler({ alerts, now: () => clock, onFire: (ev) => fired.push(ev.time),
                            loadFired: () => saved, saveFired: (f) => { saved = f; } });
assert.deepStrictEqual(s.tick(), []);                // no schedule yet
s.setSchedule(schedule);
s.tick();
assert.deepStrictEqual(fired, []);
clock = new Date(2026, 8, 27, 8, 45, 0); s.tick();
clock = new Date(2026, 8, 27, 8, 45, 1); s.tick();   // same event not repeated
assert.deepStrictEqual(fired, ['08:45']);

// "restart" of the app within the window: persisted fired set prevents a replay
const s2 = createScheduler({ alerts, now: () => clock, onFire: (ev) => fired.push('dup ' + ev.time),
                             loadFired: () => saved, saveFired: (f) => { saved = f; } });
s2.setSchedule(schedule); s2.tick();
assert.deepStrictEqual(fired, ['08:45']);

// laptop asleep through 09:30, wakes at 09:40 → no stale alert
clock = new Date(2026, 8, 27, 9, 40, 0); s.tick();
assert.deepStrictEqual(fired, ['08:45']);
// asleep, wakes 30 s after an alert → still fires (within 90 s)
clock = new Date(2026, 8, 28, 8, 0, 30); s.tick();    // Monday 08:00 start
assert.deepStrictEqual(fired, ['08:45', '08:00']);
// next alert across days: Monday 09:31 → next is Sunday
clock = new Date(2026, 8, 28, 9, 31, 0);
const nx = s.next();
assert.strictEqual(nx.day, 6); assert.strictEqual(nx.ev.time, '08:00');
console.log('desktop scheduler: all tests passed');
