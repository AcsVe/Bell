/* Android/iOS bridge (Capacitor). Registers every alert with the operating
 * system as a weekly repeating alarm, so it fires on time with the app
 * closed, the screen locked and no network — indefinitely.
 *   Android: native BellAlarm module → AlarmManager.setAlarmClock (exact, Doze-exempt),
 *            re-armed weekly by the app's own receiver, restored after reboot.
 *   iOS:     UNCalendarNotificationTrigger(repeats: true) via LocalNotifications.
 * Written as a factory so the Node tests can drive it with mock plugins. */
(function (root) {
  'use strict';
  const IOS_LIMIT = 60;          // iOS keeps at most 64 pending notifications
  const CHANNEL = 'bell-alerts';

  // Python weekday (Mon=0..Sun=6) → Capacitor/iOS weekday (Sun=1..Sat=7)
  const capWeekday = (pyWd) => ((pyWd + 1) % 7) + 1;

  function createBridge(Capacitor, Alerts, ls, cfg) {
    const P = Capacitor.Plugins;
    const LN = P.LocalNotifications;
    const BA = P.BellAlarm;          // Android native module (see android/app/src/main/java)
    const platform = Capacitor.getPlatform();
    const isIOS = platform === 'ios';
    const isAndroid = platform === 'android';

    const txt = (lang, ar, en) => (lang === 'en' ? en : ar);

    async function getCode() {
      if (P.Preferences) {
        const r = await P.Preferences.get({ key: 'code' });
        if (r && r.value) return r.value;
      }
      return ls.getItem('bell:code') || '';
    }

    async function ensureIOSSound() {
      // iOS plays custom notification sounds from Library/Sounds; copy the
      // bundled chime there once (Filesystem plugin), no Xcode changes needed.
      if (!isIOS || !P.Filesystem || ls.getItem('bell:iosSound') === '1') return;
      try {
        const res = await fetch('sounds/chime.wav');
        const buf = new Uint8Array(await res.arrayBuffer());
        let bin = '';
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        await P.Filesystem.writeFile({ path: 'Sounds/chime.wav', data: btoa(bin), directory: 'LIBRARY', recursive: true });
        ls.setItem('bell:iosSound', '1');
      } catch (e) { /* default sound is used instead */ }
    }

    async function permissionIssues(lang) {
      const issues = [];
      let perm = await LN.checkPermissions();
      if (perm.display !== 'granted') perm = await LN.requestPermissions();
      if (perm.display !== 'granted') {
        issues.push({ key: 'notifications', text: txt(lang,
          'اسمح للتطبيق بالإشعارات من إعدادات الجهاز، وإلا لن يظهر التنبيه.',
          'Allow notifications for this app in device settings, or alerts cannot appear.') });
      }
      if (isAndroid && BA) {
        const st = await BA.status();
        if (!st.exact) {
          issues.push({ key: 'exact', text: txt(lang,
            'فعّل «المنبهات والتذكيرات» ليرن التنبيه في الدقيقة المحددة تماماً.',
            'Turn on “Alarms & reminders” so alerts ring at the exact minute.'),
            action: () => BA.openExactAlarmSettings() });
        }
        if (!st.battery) {
          issues.push({ key: 'battery', text: txt(lang,
            'اسمح للتطبيق بالعمل دون قيود البطارية (ضروري في Samsung وXiaomi وHuawei). إن لم تظهر النافذة: الإعدادات ← التطبيقات ← جرس الحصص ← البطارية ← «غير مقيّد».',
            'Let the app run without battery restrictions (required on Samsung, Xiaomi, Huawei). If no dialog appears: Settings → Apps → School Bell → Battery → “Unrestricted”.'),
            action: () => BA.openBatterySettings() });
        }
      }
      if (isIOS) {
        issues.push({ key: 'silent', text: txt(lang,
          'على iPhone/iPad يظهر التنبيه والجهاز صامت لكن بدون صوت. أبقِ مفتاح الصامت مغلقاً على الجهاز المخصص للتنبيه.',
          'On iPhone/iPad the alert shows in silent mode but without sound. Keep the ring/silent switch on ring.') });
      }
      return issues;
    }

    function buildRequests(schedule, lang, now) {
      let plan = Alerts.weeklyPlan(schedule, lang);
      let mode = 'weekly';
      if (isIOS && plan.length > IOS_LIMIT) {
        // Too many distinct weekly times for iOS: fall back to the next ~60
        // one-shot alerts; refreshed on every launch/sync.
        plan = Alerts.rollingPlan(schedule, lang, now, 21, IOS_LIMIT);
        mode = 'rolling';
      }
      if (isAndroid && BA) {
        // Native weekly alarms: Calendar weekday (Sun=1..Sat=7) = capWeekday.
        const alarms = plan.map((p) => ({ id: p.id, weekday: capWeekday(p.pyWeekday), hour: p.hour,
                                          minute: p.minute, title: p.title, body: p.body || '' }));
        return { mode: 'android', alarms, requests: [], signature: `android|${lang}|${Alerts.planSignature(plan)}` };
      }
      const requests = plan.map((p) => ({
        id: p.id,
        title: p.title,
        body: p.body || '',
        channelId: CHANNEL,
        sound: 'chime.wav',
        autoCancel: true,
        extra: { time: p.time, stageId: p.stageId },
        schedule: mode === 'weekly'
          ? { on: { weekday: capWeekday(p.pyWeekday), hour: p.hour, minute: p.minute, second: 0 },
              allowWhileIdle: true }
          : { at: p.at, allowWhileIdle: true },
      }));
      return { mode, requests, signature: `${mode}|${lang}|${Alerts.planSignature(plan)}` };
    }

    async function onSchedule(schedule, lang, now) {
      now = now || new Date();
      const built = buildRequests(schedule, lang, now);
      const { mode, requests, signature } = built;
      if (mode === 'android') {
        const st = await BA.status();
        if (ls.getItem('bell:sig') !== signature || st.count !== built.alarms.length) {
          await permissionIssues(lang);                 // prompts for notifications once
          await BA.setAlarms({ alarms: built.alarms });
          ls.setItem('bell:sig', signature);
        }
        const now2 = await BA.status();
        if (!now2.notifications) return txt(lang, 'الإشعارات غير مسموحة لهذا التطبيق', 'Notifications are not allowed for this app');
        return status(lang, 'weekly', now2.count) + (now2.exact ? '' : txt(lang, ' — غير دقيق: فعّل المنبهات والتذكيرات', ' — not exact: enable Alarms & reminders'));
      }
      const pending = (await LN.getPending()).notifications || [];
      // Weekly alerts never run out; skip the costly re-registration when
      // nothing changed. Rolling (iOS overflow) always tops up.
      if (mode === 'weekly' && ls.getItem('bell:sig') === signature && pending.length === requests.length) {
        return status(lang, mode, requests.length);
      }
      await ensureIOSSound();
      const issues = await permissionIssues(lang);
      if (issues.some((i) => i.key === 'notifications')) return issues[0].text;
      if (pending.length) await LN.cancel({ notifications: pending.map((n) => ({ id: n.id })) });
      for (let i = 0; i < requests.length; i += 50) {
        await LN.schedule({ notifications: requests.slice(i, i + 50) });
      }
      ls.setItem('bell:sig', signature);
      return status(lang, mode, requests.length);
    }

    function status(lang, mode, n) {
      return mode === 'weekly'
        ? txt(lang, `${n} تنبيه أسبوعي مسجّل في الجهاز`, `${n} weekly alerts registered on this device`)
        : txt(lang, `${n} تنبيه قادم مسجّل (يتجدد عند فتح التطبيق)`, `${n} upcoming alerts registered (renewed when the app opens)`);
    }

    return {
      platform,
      nativeSound: true,        // the OS notification plays the chime
      inAppAlerts: true,        // still show the big overlay when the app is open
      get apiBase() { return ls.getItem('bell:server') || cfg.defaultServer; },
      getCode,
      async saveLogin(code, server) {
        ls.setItem('bell:server', server);
        ls.setItem('bell:code', code);
        if (P.Preferences) await P.Preferences.set({ key: 'code', value: code });
        ls.removeItem('bell:sig');
      },
      async logout() {
        ls.removeItem('bell:code'); ls.removeItem('bell:sig');
        if (P.Preferences) await P.Preferences.remove({ key: 'code' });
        if (isAndroid && BA) { await BA.clear(); return; }
        const pending = (await LN.getPending()).notifications || [];
        if (pending.length) await LN.cancel({ notifications: pending.map((n) => ({ id: n.id })) });
      },
      goLogin() { root.location.href = 'login.html'; },
      async test(title, body) {   // real OS alert, to check sound/volume on this device
        if (isAndroid && BA) return BA.testAlarm({ title, body });
        return LN.schedule({ notifications: [{ id: 999999, title, body, sound: 'chime.wav',
                                               schedule: { at: new Date(Date.now() + 1500) } }] });
      },
      setup: permissionIssues,
      onSchedule,
      _buildRequests: buildRequests,
    };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { createBridge, capWeekday };
  } else {
    // alerts.js loads after this file, so resolve BellAlerts lazily.
    const lazy = { weeklyPlan: (...a) => root.BellAlerts.weeklyPlan(...a),
                   rollingPlan: (...a) => root.BellAlerts.rollingPlan(...a),
                   planSignature: (...a) => root.BellAlerts.planSignature(...a) };
    root.BellNative = createBridge(root.Capacitor, lazy, root.localStorage, root.BELL_CONFIG || {});
    // Tapping a notification opens the app on the alert screen.
    const LN = root.Capacitor.Plugins.LocalNotifications;
    if (LN && LN.addListener) {
      LN.addListener('localNotificationActionPerformed', () => {
        if (!/app\.html$/.test(root.location.pathname)) root.location.href = 'app.html';
      });
    }
  }
})(typeof self !== 'undefined' ? self : this);
