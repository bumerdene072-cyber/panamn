const SHELL = 'duu-salgagch-shell-v2';
const OFFLINE = '/static/offline.html';
const ASSETS = [
  OFFLINE, '/static/style.css', '/static/main.js', '/static/manifest.webmanifest',
  '/static/icon-192.png', '/static/icon-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL).then(cache => cache.addAll(ASSETS)));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('duu-salgagch-shell-') && key !== SHELL)
      .map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  const {request} = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate' && url.pathname === '/') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE)));
  } else if (ASSETS.includes(url.pathname)) {
    event.respondWith(fetch(request).catch(() => caches.match(url.pathname)));
  }
});
