// Minimal service worker. Its main job here is just to exist, since
// browsers require one before they'll offer "Install app". It also
// caches the app shell so the page itself loads even with a flaky
// connection (the live data still needs network, of course).

const CACHE_NAME = "departures-shell-v1";
const SHELL_FILES = ["/", "/index.html", "/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // Only handle GET requests for the app shell itself - always go to the
  // network for API calls (/api/...) so departure data is never stale.
  if (event.request.url.includes("/api/")) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
