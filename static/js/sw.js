/* Service worker — served from /sw.js so its scope is the whole site.
 * The display page and its assets are cached so the screen keeps working with
 * no network. The schedule itself lives in IndexedDB (managed by display.js). */
const VERSION = 'bell-v2';
const SHELL = [
  '/app',
  '/static/css/display.css',
  '/static/js/alerts.js',
  '/static/js/display.js',
  '/static/js/store.js',
  '/static/sounds/chime.wav',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(req, fallbackUrl) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok && !res.redirected && res.type === 'basic') cache.put(req, res.clone());
    return res;
  } catch (err) {
    return (await cache.match(req, { ignoreSearch: true })) ||
           (fallbackUrl && await cache.match(fallbackUrl)) ||
           new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } });
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === 'opaque') cache.put(req, res.clone());
  return res;
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // Google Fonts: cache-first so the Arabic font still renders offline.
  if (url.host === 'fonts.googleapis.com' || url.host === 'fonts.gstatic.com') {
    e.respondWith(cacheFirst(req).catch(() => new Response('', { status: 504 })));
    return;
  }
  if (url.origin !== location.origin) return;
  // Schedule API and admin pages always go to the network.
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) return;

  if (req.mode === 'navigate') {
    // Pages: fresh when online, cached copy when offline (/app as last resort).
    e.respondWith(networkFirst(req, url.pathname.startsWith('/login') ? null : '/app'));
    return;
  }
  // Versioned media (?v=) and static assets: cache-first.
  if (url.pathname.startsWith('/media/')) { e.respondWith(cacheFirst(req)); return; }
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    e.respondWith(networkFirst(req));
  }
});

// display.js asks us to pre-cache stage logos/backgrounds/tone after a sync.
self.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'precache' && Array.isArray(e.data.urls)) {
    e.waitUntil(caches.open(VERSION).then((c) =>
      Promise.all(e.data.urls.map((u) => c.match(u).then((hit) => hit || c.add(u).catch(() => null))))));
  }
});
