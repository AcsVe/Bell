#!/usr/bin/env node
/* Builds the offline web bundle for the native shells from the same files the
 * PWA uses, so there is one alert engine everywhere.
 *   node native/build-web.js mobile   → mobile/www
 *   node native/build-web.js desktop  → desktop/www
 * BELL_SERVER=https://your-app.onrender.com sets the default server address. */
const fs = require('fs');
const path = require('path');

const target = process.argv[2];
if (!['mobile', 'desktop'].includes(target)) {
  console.error('usage: node native/build-web.js mobile|desktop');
  process.exit(1);
}
const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, target, 'www');
const server = (process.env.BELL_SERVER || 'https://bell-zrv4.onrender.com').replace(/\/+$/, '');

fs.rmSync(OUT, { recursive: true, force: true });
for (const d of ['js', 'css', 'icons', 'sounds']) fs.mkdirSync(path.join(OUT, d), { recursive: true });

const cp = (from, to) => fs.copyFileSync(path.join(ROOT, from), path.join(OUT, to));
cp('static/js/alerts.js', 'js/alerts.js');
cp('static/js/store.js', 'js/store.js');
cp('static/js/display.js', 'js/display.js');
cp('static/css/display.css', 'css/display.css');
cp('static/sounds/chime.wav', 'sounds/chime.wav');
cp('static/icons/icon-192.png', 'icons/icon-192.png');
cp('static/icons/icon-512.png', 'icons/icon-512.png');
cp('native/web/login.html', 'login.html');
cp('native/web/index.html', 'index.html');
cp(`native/web/bridge-${target}.js`, 'js/bridge.js');
if (target === 'desktop') cp('native/web/alert.html', 'alert.html');

fs.writeFileSync(path.join(OUT, 'config.js'),
  `window.BELL_CONFIG = ${JSON.stringify({ defaultServer: server, platform: target })};\n`);

// app.html = the PWA display template with relative paths and the native bridge.
let html = fs.readFileSync(path.join(ROOT, 'templates/display.html'), 'utf8');
html = html
  .replace(/\{\{ lang \}\}/g, 'ar').replace(/\{\{ dir \}\}/g, 'rtl')
  .replace(/\{\{ asset\('([^']+)'\) \}\}/g, '$1')
  .replace(/^.*rel="manifest".*\n/m, '')
  .replace(/\/static\/css\//g, 'css/').replace(/\/static\/js\//g, 'js/').replace(/\/static\/icons\//g, 'icons/')
  .replace('<script src="js/store.js"></script>',
           '<script src="config.js"></script>\n<script src="js/bridge.js"></script>\n<script src="js/store.js"></script>');
if (/\{\{|\{%/.test(html)) throw new Error('unrendered template syntax left in app.html');
fs.writeFileSync(path.join(OUT, 'app.html'), html);

console.log(`web bundle → ${path.relative(ROOT, OUT)}  (server: ${server})`);
