/* Main-process alert scheduler for the desktop app. Node timers in the main
 * process are never throttled like a background browser tab, so the alert
 * fires on the second as long as the computer is awake — offline, with every
 * window closed. Pure logic (no Electron imports) so it is unit-tested. */
'use strict';

const FIRE_WINDOW_SEC = 90;   // a sleep/resume never replays old alerts

function createScheduler({ alerts, now = () => new Date(), onFire, loadFired, saveFired }) {
  let schedule = null;
  let fired = new Set((loadFired && loadFired()) || []);
  let dayKey = '';
  let events = [];

  const keyOf = (d) => `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;

  function setSchedule(s) {
    schedule = s;
    dayKey = '';            // recompute today's events
  }

  function tick() {
    if (!schedule) return [];
    const t = now();
    const k = keyOf(t);
    if (k !== dayKey) {
      dayKey = k;
      events = alerts.eventsForDate(schedule, t);
      fired = new Set([...fired].filter((f) => f.startsWith(k + '@')));
    }
    const due = alerts.dueEvents(events, t, FIRE_WINDOW_SEC).filter((ev) => !fired.has(`${k}@${ev.min}`));
    due.forEach((ev) => {
      fired.add(`${k}@${ev.min}`);
      if (saveFired) saveFired([...fired]);
      onFire(ev, schedule);
    });
    return due;
  }

  function next() {
    if (!schedule) return null;
    const t = now();
    const today = alerts.nextEvent(alerts.eventsForDate(schedule, t), t);
    if (today) return { day: 0, ev: today };
    for (let d = 1; d <= 7; d++) {
      const day = new Date(t.getFullYear(), t.getMonth(), t.getDate() + d);
      const evs = alerts.eventsForDate(schedule, day);
      if (evs.length) return { day: d, ev: evs[0] };
    }
    return null;
  }

  return { setSchedule, tick, next, get schedule() { return schedule; } };
}

module.exports = { createScheduler, FIRE_WINDOW_SEC };
