/* Drives the real desktop app (run by tests/e2e_desktop.py under Xvfb). */
'use strict';
const fs = require('fs');
const path = require('path');

module.exports = function selftest(app, ctx) {
  const OUT = process.env.BELL_OUT;
  const log = (m) => fs.appendFileSync(path.join(OUT, 'selftest.log'), `${new Date().toISOString()} ${m}\n`);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const result = {};
  const until = async (fn, ms, what) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { try { const v = await fn(); if (v) return v; } catch (e) { /* retry */ } await sleep(300); }
    throw new Error('timeout: ' + what);
  };
  const js = (w, code) => w.webContents.executeJavaScript(code);

  (async () => {
    try {
      const main = await until(() => ctx.getMain(), 10000, 'main window');
      await until(async () => (await js(main, 'location.pathname')).endsWith('login.html'), 15000, 'login page');
      log('login page loaded');
      await js(main, `document.getElementById('server').value = ${JSON.stringify(process.env.BELL_SERVER_URL)};
                      document.getElementById('code').value = ${JSON.stringify(process.env.BELL_CODE)};
                      document.getElementById('f').requestSubmit(); true`);
      await until(async () => (await js(main, 'location.pathname')).endsWith('app.html'), 20000, 'app page');
      await until(() => ctx.scheduler.schedule, 20000, 'schedule handed to main process');
      log('schedule cached in main process');
      await sleep(1500);
      fs.writeFileSync(path.join(OUT, 'main.png'), (await main.webContents.capturePage()).toPNG());
      result.status = await js(main, "document.getElementById('syncInfo').textContent");
      fs.writeFileSync(path.join(OUT, 'synced'), '1');          // harness now kills the server
      const w = await until(() => ctx.getAlerts()[0], 5 * 60 * 1000, 'alert window');
      result.firedAt = new Date().toTimeString().slice(0, 8);
      log('alert window opened at ' + result.firedAt);
      await sleep(2000);
      result.alertText = await js(w, "document.getElementById('alertText').innerText");
      result.alertTime = await js(w, "document.getElementById('alertTime').innerText");
      result.soundPlaying = await js(w, "!document.getElementById('tone').paused");
      result.alwaysOnTop = w.isAlwaysOnTop();
      result.fullscreen = w.isFullScreen() || w.getBounds().width >= 1000;
      fs.writeFileSync(path.join(OUT, 'alert.png'), (await w.webContents.capturePage()).toPNG());
      result.serverDown = fs.existsSync(path.join(OUT, 'server-stopped'));
      result.ok = true;
    } catch (e) {
      result.ok = false; result.error = String(e && e.stack || e);
    }
    fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(result, null, 2));
    app.exit(0);
  })();
};
