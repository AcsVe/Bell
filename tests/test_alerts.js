const assert = require('assert');
const A = require('../static/js/alerts.js');

const P = (n, kind, ar, en, start, end) => ({ n, kind, ar, en, start, end });
const day = [
  P(1, 'class', 'الحصة الأولى', 'Period 1', '08:00', '08:45'),
  P(2, 'class', 'الحصة الثانية', 'Period 2', '08:45', '09:30'),
  P(3, 'break', 'فترة الاستراحة', 'Break', '09:30', '09:50'),
  P(4, 'class', 'الحصة الثالثة', 'Period 3', '09:50', '10:35'),
];

// back-to-back periods → one combined alert per boundary + start and end of day
let ev = A.dayEvents(day);
assert.deepStrictEqual(ev.map((e) => e.ar), [
  'بداية الحصة الأولى',
  'انتهت الحصة الأولى - بداية الحصة الثانية',
  'انتهت الحصة الثانية - بداية فترة الاستراحة',
  'انتهت فترة الاستراحة - بداية الحصة الثالثة',
  'انتهت الحصة الثالثة',
]);
assert.strictEqual(ev[1].en, 'End of Period 1 — Start of Period 2');
assert.deepStrictEqual(ev.map((e) => e.min), [480, 525, 570, 590, 635]);

// a gap (defensive) splits into end + start
ev = A.dayEvents([day[0], P(2, 'class', 'ب', 'B', '08:50', '09:30')]);
assert.deepStrictEqual(ev.map((e) => [e.min, e.kind]), [[480, 'start'], [525, 'end'], [530, 'start'], [570, 'end']]);

// weekday mapping: 2026-09-27 is a Sunday → python 6
const sunday = new Date(2026, 8, 27, 8, 45, 10);
assert.strictEqual(A.pyWeekday(sunday), 6);

// teacher with two sections in the same grade + one in another stage with a different time
const schedule = {
  targets: [
    { sectionId: 1, stageId: 1, gradeId: 10, ar: 'الأول - أ', en: '1-A' },
    { sectionId: 2, stageId: 1, gradeId: 10, ar: 'الأول - ب', en: '1-B' },
    { sectionId: 3, stageId: 2, gradeId: 20, ar: 'العاشر - أ', en: '10-A' },
  ],
  days: {
    10: { 6: day },
    20: { 6: [P(1, 'class', 'الحصة الأولى', 'Period 1', '07:45', '08:45'),
              P(2, 'class', 'الحصة الثانية', 'Period 2', '08:45', '09:40')] },
  },
};
const evs = A.eventsForDate(schedule, sunday);
const at845 = evs.find((e) => e.time === '08:45');
assert.strictEqual(at845.items.length, 1, 'same text at same minute merged');
assert.strictEqual(at845.items[0].targets.length, 3);
assert.strictEqual(evs[0].time, '07:45');

// due window: 10 s after 08:45 fires, 2 minutes after does not
assert.strictEqual(A.dueEvents(evs, sunday, 90).length, 1);
assert.strictEqual(A.dueEvents(evs, new Date(2026, 8, 27, 8, 47, 0), 90).length, 0);
assert.strictEqual(A.nextEvent(evs, sunday).time, '09:30');

// no school on Friday (python 4)
assert.deepStrictEqual(A.eventsForDate(schedule, new Date(2026, 9, 2, 9, 0)), []);

// current status
const st = A.currentStatus(schedule, sunday);
assert.strictEqual(st[0].current.ar, 'الحصة الثانية');
assert.strictEqual(st[2].current.ar, 'الحصة الثانية');
assert.strictEqual(st[0].dayEnd, '10:35');

// weekly plan: stable ids, one per (weekday, minute), texts merged for a teacher
schedule.who = { type: 'teacher', ar: 'سارة', en: 'Sara' };
const plan = A.weeklyPlan(schedule, 'ar');
assert.ok(plan.every((p) => p.pyWeekday === 6), 'only Sunday has periods in this fixture');
const p845 = plan.find((p) => p.time === '08:45');
assert.strictEqual(p845.id, 6 * 10000 + 525);
assert.strictEqual(p845.title, 'انتهت الحصة الأولى - بداية الحصة الثانية');
assert.ok(p845.body.includes('الأول - أ') && p845.body.includes('العاشر - أ'));
assert.strictEqual(new Set(plan.map((p) => p.id)).size, plan.length);
assert.strictEqual(A.weeklyPlan(schedule, 'en').find((p) => p.time === '08:45').title,
  'End of Period 1 — Start of Period 2');
// section device: body is the section name
const secSched = Object.assign({}, schedule, { targets: [schedule.targets[0]], who: { type: 'section', ar: 'الأول - أ', en: '1-A' } });
assert.strictEqual(A.weeklyPlan(secSched, 'ar')[0].body, 'الأول - أ');
// a full school week fits the iOS 64-alert limit as repeating alerts
const week = { targets: [schedule.targets[0]], days: { 10: {} } };
[6, 0, 1, 2, 3].forEach((wd) => { week.days[10][wd] = day.concat([
  P(5, 'class', 'ر', 'P4', '10:35', '11:20'), P(6, 'class', 'خ', 'P5', '11:20', '12:05'),
  P(7, 'class', 'س', 'P6', '12:05', '12:50'), P(8, 'class', 'ث', 'P7', '12:50', '13:35')]); });
assert.strictEqual(A.weeklyPlan(week, 'ar').length, 5 * 9);
// rolling plan skips the past and respects max
const roll = A.rollingPlan(week, 'ar', new Date(2026, 8, 27, 9, 0), 14, 64);
assert.strictEqual(roll.length, 64);
assert.ok(roll[0].at > new Date(2026, 8, 27, 9, 0) && roll[0].time === '09:30');
assert.notStrictEqual(A.planSignature(plan), A.planSignature(A.weeklyPlan(week, 'ar')));
assert.strictEqual(A.planSignature(plan), A.planSignature(A.weeklyPlan(schedule, 'ar')));

console.log('alerts.js: all tests passed');
