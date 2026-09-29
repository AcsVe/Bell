/* Pure alert-computation logic, shared by the display page and the Node tests.
 * Input: the schedule payload from /api/schedule (cached in IndexedDB).
 * Nothing here touches the network — alerts are computed from the device clock. */
(function (root) {
  'use strict';

  function toMin(hhmm) { const [h, m] = hhmm.split(':').map(Number); return h * 60 + m; }

  // JS getDay(): Sun=0..Sat=6 → Python weekday(): Mon=0..Sun=6 (what the server uses)
  function pyWeekday(date) { return (date.getDay() + 6) % 7; }

  function texts(endP, startP) {
    if (endP && startP) return {
      ar: `انتهت ${endP.ar} - بداية ${startP.ar}`,
      en: `End of ${endP.en} — Start of ${startP.en}`,
      kind: 'transition', next: startP };
    if (endP) return { ar: `انتهت ${endP.ar}`, en: `End of ${endP.en}`, kind: 'end', next: null };
    return { ar: `بداية ${startP.ar}`, en: `Start of ${startP.en}`, kind: 'start', next: startP };
  }

  /* One day's periods → alert events. Back-to-back periods (the normal case)
   * give a single combined alert; a gap (if one ever exists) gives separate
   * end and start alerts. The first period's start also alerts. */
  function dayEvents(periods) {
    const ps = periods.slice().sort((a, b) => toMin(a.start) - toMin(b.start) || a.n - b.n);
    const ev = [];
    ps.forEach((p, i) => {
      if (i === 0) ev.push(Object.assign({ min: toMin(p.start) }, texts(null, p)));
      const next = ps[i + 1];
      if (next && next.start === p.end) {
        ev.push(Object.assign({ min: toMin(p.end) }, texts(p, next)));
      } else {
        ev.push(Object.assign({ min: toMin(p.end) }, texts(p, null)));
        if (next) ev.push(Object.assign({ min: toMin(next.start) }, texts(null, next)));
      }
    });
    return ev;
  }

  /* All alerts for a date across every target (a teacher may cover many
   * sections/stages). Same time + same text is merged into one item listing
   * every section it applies to. Returns [{min, time, items:[{ar,en,stageId,targets}]}]. */
  function eventsForDate(schedule, date) { return eventsForWeekday(schedule, pyWeekday(date)); }

  function eventsForWeekday(schedule, pyWd) {
    if (!schedule || !schedule.targets) return [];
    const wd = String(pyWd);
    const byMin = new Map();
    schedule.targets.forEach((t) => {
      const days = (schedule.days[String(t.gradeId)] || {})[wd];
      if (!days || !days.length) return;
      dayEvents(days).forEach((e) => {
        if (!byMin.has(e.min)) byMin.set(e.min, new Map());
        const items = byMin.get(e.min);
        const key = e.ar;   // identical text at the same minute is shown once
        if (!items.has(key)) items.set(key, { ar: e.ar, en: e.en, kind: e.kind, stageId: t.stageId, targets: [] });
        const its = items.get(key);
        if (!its.targets.some((x) => x.ar === t.ar)) its.targets.push({ ar: t.ar, en: t.en });
      });
    });
    return [...byMin.entries()].sort((a, b) => a[0] - b[0]).map(([min, items]) => ({
      min, time: `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`,
      items: [...items.values()],
    }));
  }

  /* Text for an OS notification: title = the alert, body = who/where. */
  function notificationText(ev, schedule, lang) {
    const pick = (o) => (lang === 'en' ? o.en || o.ar : o.ar);
    const title = ev.items.map(pick).join(' | ');
    let body;
    if (schedule.who && schedule.who.type === 'teacher') {
      body = ev.items.map((it) => it.targets.map(pick).join('، ')).join(' | ');
    } else {
      body = schedule.who ? pick(schedule.who) : '';
    }
    return { title, body };
  }

  /* Weekly repeating plan for OS schedulers: one entry per (weekday, minute).
   * Registered once, these fire every week forever — no network, no app launch.
   * id is stable: weekday*10000 + minute-of-day. */
  function weeklyPlan(schedule, lang) {
    const out = [];
    for (let wd = 0; wd < 7; wd++) {
      eventsForWeekday(schedule, wd).forEach((ev) => {
        const txt = notificationText(ev, schedule, lang);
        out.push({ id: wd * 10000 + ev.min, pyWeekday: wd, hour: Math.floor(ev.min / 60),
                   minute: ev.min % 60, time: ev.time, title: txt.title, body: txt.body,
                   stageId: ev.items[0].stageId });
      });
    }
    return out;
  }

  /* One-shot plan for the next `days` days starting at `from` (used when a
   * platform can't hold the whole weekly plan, e.g. iOS's 64-alert limit). */
  function rollingPlan(schedule, lang, from, days, max) {
    const out = [];
    const start = new Date(from.getFullYear(), from.getMonth(), from.getDate());
    for (let d = 0; d < days && out.length < max; d++) {
      const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + d);
      eventsForDate(schedule, day).forEach((ev) => {
        const at = new Date(day.getFullYear(), day.getMonth(), day.getDate(), Math.floor(ev.min / 60), ev.min % 60, 0);
        if (at <= from || out.length >= max) return;
        const txt = notificationText(ev, schedule, lang);
        out.push({ id: 700000 + out.length, at, time: ev.time, title: txt.title, body: txt.body,
                   stageId: ev.items[0].stageId });
      });
    }
    return out;
  }

  /* Short fingerprint of what the OS has registered, to skip needless re-registration. */
  function planSignature(plan) {
    let h = 0;
    const s = plan.map((p) => `${p.id}|${p.title}|${p.body}`).join('\n');
    for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    return `${plan.length}:${h}`;
  }

  /* What is happening right now for each target (for the idle screen). */
  function currentStatus(schedule, date) {
    const wd = String(pyWeekday(date));
    const nowMin = date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60;
    const out = [];
    (schedule && schedule.targets || []).forEach((t) => {
      const days = ((schedule.days[String(t.gradeId)] || {})[wd] || []).slice()
        .sort((a, b) => toMin(a.start) - toMin(b.start));
      let cur = null;
      days.forEach((p) => { if (nowMin >= toMin(p.start) && nowMin < toMin(p.end)) cur = p; });
      out.push({ target: t, current: cur, dayStart: days[0] ? days[0].start : null,
                 dayEnd: days.length ? days[days.length - 1].end : null, schoolDay: days.length > 0 });
    });
    return out;
  }

  /* Events that should fire at `date`: due within the last `windowSec`
   * seconds (so a tab that was briefly throttled still alerts, but a device
   * switched on mid-morning doesn't replay the whole day). */
  function dueEvents(events, date, windowSec) {
    const nowSec = date.getHours() * 3600 + date.getMinutes() * 60 + date.getSeconds();
    return events.filter((e) => { const d = nowSec - e.min * 60; return d >= 0 && d < windowSec; });
  }

  function nextEvent(events, date) {
    const nowSec = date.getHours() * 3600 + date.getMinutes() * 60 + date.getSeconds();
    return events.find((e) => e.min * 60 > nowSec) || null;
  }

  const api = { toMin, pyWeekday, dayEvents, eventsForDate, eventsForWeekday, notificationText,
                weeklyPlan, rollingPlan, planSignature, currentStatus, dueEvents, nextEvent };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.BellAlerts = api;
})(typeof self !== 'undefined' ? self : this);
