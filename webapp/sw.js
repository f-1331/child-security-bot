// Child Security Service Worker (PWA 100% Offline Resilience & Background Sync)
const CACHE_NAME = "child-security-v17";
const API_CACHE_NAME = "child-security-api-v17";

const STATIC_ASSETS = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/manifest.json",
  "/favicon.png",
  "/logo.png?v=17",
  "/icon-192.png",
  "/icon-512.png",
  "/apple-touch-icon.png",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
  "https://cdn.tailwindcss.com",
  "https://unpkg.com/lucide@latest",
  "https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"
];

// Install: pre-cache static assets and external libraries
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async (cache) => {
      for (const asset of STATIC_ASSETS) {
        try {
          await cache.add(asset);
        } catch (err) {
          console.warn("PWA pre-cache warning for asset:", asset, err);
        }
      }
    })
  );
  self.skipWaiting();
});

// Activate: purge outdated caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME && key !== API_CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch: Smart network-first with automatic offline fallback
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 1. Handle API GET requests (Tasks, Geofences, Status, Telemetry)
  if (req.method === "GET" && url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(req)
        .then((networkRes) => {
          if (networkRes && networkRes.status === 200) {
            const resClone = networkRes.clone();
            caches.open(API_CACHE_NAME).then((cache) => cache.put(req, resClone));
          }
          return networkRes;
        })
        .catch(async () => {
          const cached = await caches.match(req);
          if (cached) return cached;
          return new Response(JSON.stringify({ status: "offline", offline: true }), {
            headers: { "Content-Type": "application/json" }
          });
        })
    );
    return;
  }

  // Skip non-GET requests (POST/PUT/DELETE handled via local offline queue in app.js)
  if (req.method !== "GET") {
    return;
  }

  // 2. Handle CDN Scripts & CSS (Leaflet, Tailwind, Lucide, Fonts)
  if (url.origin !== self.location.origin) {
    if (
      url.hostname.includes("unpkg.com") ||
      url.hostname.includes("tailwindcss.com") ||
      url.hostname.includes("jsdelivr.net") ||
      url.hostname.includes("googleapis.com") ||
      url.hostname.includes("gstatic.com")
    ) {
      event.respondWith(
        caches.match(req).then((cached) => {
          if (cached) return cached;
          return fetch(req)
            .then((res) => {
              if (res && res.status === 200) {
                const clone = res.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(req, clone));
              }
              return res;
            })
            .catch(() => cached || new Response("", { status: 200 }));
        })
      );
      return;
    }
  }

  // 3. Handle App Shell Navigation & Static Assets: Stale-While-Revalidate (Instant 0ms launch)
  event.respondWith(
    caches.match(req).then((cachedRes) => {
      const fetchPromise = fetch(req)
        .then((networkRes) => {
          if (networkRes && networkRes.status === 200) {
            const resClone = networkRes.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, resClone));
          }
          return networkRes;
        })
        .catch(async () => {
          if (cachedRes) return cachedRes;
          if (req.mode === "navigate") {
            return (await caches.match("/index.html")) || (await caches.match("/"));
          }
          return new Response("Offline", { status: 503, statusText: "Offline" });
        });

      // Ultra-fast instant response: return cache immediately if available, revalidate in background!
      return cachedRes || fetchPromise;
    })
  );
});

// Push notification receiver
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Child Security", body: event.data ? event.data.text() : "Yangi xabarnoma" };
  }

  const title = data.title || "Child Security 🛡️";
  const options = {
    body: data.body || "Xavfsizlik monitoringi yangilanishi",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    vibrate: [200, 100, 200],
    data: {
      url: data.url || "/"
    }
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Notification click event
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url === targetUrl && "focus" in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
