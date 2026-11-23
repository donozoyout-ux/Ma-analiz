/* Maç Analiz Service Worker - PWA & Web Push & TWA */
const CACHE_NAME = 'mac-analiz-shell-v1';
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
      fetch(event.request).catch((err) => {
        // Return 503 JSON response for failed API calls when offline
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

// Push: Receive and display verified push notifications
self.addEventListener('push', (event) => {
  let payload = {
    title: 'Maç Analiz',
    body: 'Yeni bir futbol analizi veya maç güncellemesi mevcut.',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: '/matches' },
  };

  if (event.data) {
    try {
      const data = event.data.json();
      payload = {
        title: data.title || payload.title,
        body: data.body || payload.body,
        icon: data.icon || '/icon-192.png',
        badge: data.badge || '/icon-192.png',
        tag: data.tag || 'mac-analiz-notification',
        data: data.data || { url: data.url || '/matches' },
      };
    } catch (e) {
      payload.body = event.data.text() || payload.body;
    }
  }

  const notificationOptions = {
    body: payload.body,
    icon: payload.icon,
    badge: payload.badge,
    tag: payload.tag || 'mac-analiz-alert',
    renotify: true,
    data: payload.data,
    vibrate: [200, 100, 200],
    actions: [
      { action: 'open_match', title: 'Maçı İncele' },
      { action: 'dismiss', title: 'Kapat' },
    ],
  };

  event.waitUntil(
    self.registration.showNotification(payload.title, notificationOptions)
  );
});

// Notification Click: Route to the specific match URL
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  if (event.action === 'dismiss') {
    return;
  }

  const targetUrl = (event.notification.data && event.notification.data.url)
    ? event.notification.data.url
    : '/matches';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // If a window is already open on this origin, focus it and navigate
      for (const client of windowClients) {
        if (client.url && 'focus' in client) {
          client.focus();
          return client.navigate(targetUrl);
        }
      }
      // If no window is open, open a new one
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
