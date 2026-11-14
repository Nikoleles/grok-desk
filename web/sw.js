/* Shell cache only. Plan B has no web push; data is always fetched from the network. */
const CACHE = "grok-desk-v1";
const SHELL = ["./index.html", "./app.css", "./app.js", "./chart.js", "./manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
