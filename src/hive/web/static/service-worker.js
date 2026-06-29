/* Hive PWA service worker — Ticket 040.
 *
 * Minimal offline shell. Caching is split by request class (see design.md D4):
 *   - /api/* , /sse/*           → network-only, NOT intercepted (live/auth/stream)
 *   - navigations (/, /dashboard) → network-first → cache → offline fallback
 *   - /static/landing.css        → network-first (Ticket 042: shell CSS never goes stale)
 *   - /static/*                  → stale-while-revalidate (versioned by ?v=N)
 *   - cross-origin CDN           → cache-first (opaque), runtime-cached
 *
 * Bump CACHE_VERSION on any shell/asset change so activate() drops stale caches.
 */
const CACHE_VERSION = 'hive-v4';
const OFFLINE_URL = '/static/offline.html';

// Small, safe precache: the offline shell + icons + the landing stylesheet.
// Deliberately NOT the ~3MB Babel/React CDN (runtime-cached only) so install
// stays light and the landing shell is what's guaranteed offline.
const PRECACHE_URLS = [
  OFFLINE_URL,
  '/static/landing.css',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Live data / auth / streaming — never cache; let the browser handle it.
  if (url.origin === self.location.origin && (url.pathname.startsWith('/api/') || url.pathname.startsWith('/sse/'))) {
    return;
  }

  // Navigations: network-first, fall back to the last-cached page, then the shell.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request).then((cached) => cached || caches.match(OFFLINE_URL)))
    );
    return;
  }

  // landing.css is the shell stylesheet — serve it network-first so a deploy can
  // never leave the iPad on stale CSS (Ticket 042); fall back to cache offline.
  if (url.origin === self.location.origin && url.pathname === '/static/landing.css') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Same-origin static assets: stale-while-revalidate.
  if (url.origin === self.location.origin && url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.open(CACHE_VERSION).then((cache) =>
        cache.match(request).then((cached) => {
          const network = fetch(request)
            .then((response) => {
              cache.put(request, response.clone());
              return response;
            })
            .catch(() => cached);
          return cached || network;
        })
      )
    );
    return;
  }

  // Cross-origin CDN (React/Babel/htmx/fonts): cache-first, opaque-tolerant.
  if (url.origin !== self.location.origin) {
    event.respondWith(
      caches.open(CACHE_VERSION).then((cache) =>
        cache.match(request).then(
          (cached) =>
            cached ||
            fetch(request)
              .then((response) => {
                cache.put(request, response.clone());
                return response;
              })
              .catch(() => cached)
        )
      )
    );
  }
});

// ─── Web Push (Ticket 041, ADR 0026) ───────────────────────────────────────
// The push service wakes the worker; we draw a native notification. The payload
// is JSON {title, body, url} built server-side by WebPushChannel.
self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'Hive', body: event.data ? event.data.text() : '' };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Hive', {
      body: data.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      data: { url: data.url || '/' },
      tag: data.url || 'hive', // collapse repeats for the same run/decision
    })
  );
});

// Tap → focus an open Hive window (and tell it where to focus) or open one.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          client.postMessage({ type: 'hive-focus', url: target });
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
