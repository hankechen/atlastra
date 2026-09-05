// Atlastra service worker. Kept deliberately minimal: the app's live scores,
// ratings and player data change constantly and the server already answers
// everything with Cache-Control: no-store, so this worker does NOT cache API
// responses or page HTML — it exists to make the site installable (PWA/TWA)
// and to give a real offline fallback instead of Chrome's dinosaur.
const CACHE = 'atlastra-shell-v1';
const OFFLINE_URL = '/offline.html';
const SHELL = [
  OFFLINE_URL,
  '/favicon.svg',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;

  // Page navigations: always prefer the network (scores/content change
  // constantly); only fall back to the offline page when the network is down.
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  // The handful of shell assets: serve from cache first so the offline page
  // itself (and its icon) always render, then quietly refresh in the background.
  if (SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const fresh = fetch(req).then((res) => {
          caches.open(CACHE).then((c) => c.put(req, res.clone()));
          return res;
        }).catch(() => hit);
        return hit || fresh;
      })
    );
  }
});
