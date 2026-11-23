/* Maç Analiz Service Worker - PWA & TWA (Offline-Only Shell, No Push Notifications) */
const CACHE_NAME = 'mac-analiz-shell-v2';
const PRECACHE_URLS = [
  '/offline.html',
  '/manifest.json',
  '/icon-192.png',
  '/icon-512.png',
  '/favicon.svg',
];

// Install: Precache offline shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_URLS);
    }).then(() => self.skipWaiting())
  );
});

// Activate: Claim clients and clean up legacy caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch: Strictly DO NOT cache live API responses offline!
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 1. API and dynamic data calls: Strictly Network-Only.
  // Never cache live scores, odds, or predictions offline to avoid stale/fabricated data.
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.includes('/_next/data/') ||
    event.request.method !== 'GET'
  ) {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(
          JSON.stringify({
            error: 'Çevrimdışı: Canlı API bağlantısı kurulamadı. Lütfen internet bağlantınızı kontrol edin.',
            offline: true,
          }),
          {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      })
    );
    return;
  }

  // 2. Navigation requests (HTML pages)
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => {
        return caches.match('/offline.html');
      })
    );
    return;
  }

  // 3. Static assets: Stale-While-Revalidate or Cache-First
  if (url.pathname.startsWith('/_next/static/') || url.pathname.startsWith('/icon-')) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        return fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return networkResponse;
        });
      })
    );
    return;
  }

  // Default: Network with offline fallback
  event.respondWith(
    fetch(event.request).catch(() => {
      return caches.match(event.request);
    })
  );
});
