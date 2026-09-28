// Minimal service worker for TapIn.
// Its only real job here is to exist and register successfully, which is
// what makes Chrome/PWABuilder consider the site "installable." It also
// caches the login shell so a repeat visit loads instantly even on a slow
// connection (it does NOT cache API responses, so attendance data is
// always fetched fresh).

const CACHE_NAME = 'tapin-shell-v1';
const SHELL_FILES = [
  '/login.html'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Only handle simple GET navigations for the shell; everything else
  // (API calls, POSTs, etc.) goes straight to the network untouched.
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((cached) => cached || caches.match('/login.html'))
    )
  );
});
