// Minimal service worker. Its main job here is just to exist, since
// browsers require one before they'll offer "Install app". It also
// caches the app shell so the page itself loads even with a flaky
// connection (the live data still needs network, of course).
//
// IMPORTANT: bump CACHE_NAME every time index.html/manifest.json change
// meaningfully. That's what makes the browser fetch fresh files and
// clean up the old cache - without it, phones in particular can keep
// showing an old cached version of the page indefinitely.

const CACHE_NAME = "departures-shell-v2";
const SHELL_FILES = ["/", "/index.html", "/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // Only handle GET requests for the app shell itself - always go to the
  // network for API calls (/api/...) so departure data is never stale.
  if (event.request.url.includes("/api/")) {
    return;
  }

  // Network-first for the page shell: try to get the freshest version,
  // only falling back to cache if the network is unavailable. This is
  // what actually fixes "I don't see my changes" - the old cache-first
  // approach would show stale content even when a network was available.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const responseClone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseClone));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
