/* Desktop bridge (Electron). The main process owns the timer and the alert
 * window; this page only signs in, syncs and hands the schedule over. */
(function (root) {
  'use strict';
  const E = root.electronBell;
  const cfg = root.BELL_CONFIG || {};
  root.BellNative = {
    platform: 'desktop',
    nativeSound: true,       // the alert window plays the tone
    inAppAlerts: false,      // main process shows the full-screen alert on every screen
    get apiBase() { return root.localStorage.getItem('bell:server') || cfg.defaultServer; },
    getCode: () => E.getCode(),
    async saveLogin(code, server) { root.localStorage.setItem('bell:server', server); await E.saveLogin(code, server); },
    logout: () => E.logout(),
    goLogin() { root.location.href = 'login.html'; },
    setup: async () => [],
    onSchedule: (schedule, lang) => E.setSchedule(schedule, lang),
    test: () => E.test(),
  };
})(window);
