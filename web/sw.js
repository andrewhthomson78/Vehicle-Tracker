// Offline support: keeps the app and the last-seen garage data on the phone.
// Everything is network-first, so the Mac's live data always wins when it's reachable.

const SHELL = "garage-shell-v2";
const DATA = "garage-data-v1";
const SHELL_FILES = ["/", "/app.js", "/styles.css", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];
const TIMEOUT_MS = 5000;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (![SHELL, DATA].includes(key)) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) {
    // web fonts: cache on first use
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(req, copy));
      return res;
    })));
    return;
  }
  if (url.pathname === "/calendar.ics") return;
  const cacheName = url.pathname.startsWith("/api/") ? DATA : SHELL;
  const key = req.mode === "navigate" ? "/" : req;
  event.respondWith(networkFirst(req, key, cacheName));
});

async function networkFirst(req, key, cacheName) {
  try {
    const res = await Promise.race([
      fetch(req),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), TIMEOUT_MS)),
    ]);
    if (res.ok) {
      const copy = res.clone();
      caches.open(cacheName).then((c) => c.put(key, copy));
    }
    return res;
  } catch {
    const hit = await caches.match(key);
    if (!hit) {
      return new Response(JSON.stringify({ error: "The garage is offline and this page hasn't been saved on this phone yet." }),
        { status: 503, headers: { "Content-Type": "application/json", "X-Offline": "1" } });
    }
    const headers = new Headers(hit.headers);
    headers.set("X-Offline", "1");
    return new Response(hit.body, { status: hit.status, headers });
  }
}
