// PR 328: the service worker, served from /service-worker.js (app/api/pwa.py) so its
// scope is the whole site. The route prepends `self.SW_CONFIG = {...}` - the version and
// the URLs to precache, both derived from the current static files - so any changed
// asset makes this script byte-different and the browser installs the new version.
"use strict";

const CONFIG = self.SW_CONFIG;

// Everything this worker stores is in caches named "wn-…". The precache is per version:
// an activated worker drops the older "wn-static-…" ones and touches no cache it
// didn't create (later PRs keep downloaded chapters in caches of their own).
const STATIC_CACHE = `wn-static-${CONFIG.version}`;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(CONFIG.precache)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name.startsWith("wn-static-") && name !== STATIC_CACHE)
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

const IMAGE_CACHE = "wn-images";
const FONT_CACHE = "wn-fonts";
const IMAGE_CACHE_LIMIT = 100;
const FONT_ORIGINS = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"];
const IMAGE_PATH = /\.(?:png|svg|ico|webp|jpe?g|gif)$/i;

// Which strategy a request gets. An allowlist: only the app's own static files and the
// webfonts are ever stored. Page navigations are "page" - fetched, never stored (HTML
// carries the signed-in user's sidebar). Everything else - any non-GET, /admin,
// /activity, /notifications, /settings, JSON endpoints, avatars and other uploads,
// covers hotlinked from ranobelib.me - is "network": the worker doesn't touch it.
function routeFor(method, url, mode) {
  if (method !== "GET") return "network";
  const { origin, pathname, searchParams } = new URL(url);
  if (origin !== self.location.origin) return FONT_ORIGINS.includes(origin) ? "font" : "network";
  if (mode === "navigate") return "page";
  if (!pathname.startsWith("/static/")) return "network";
  if (IMAGE_PATH.test(pathname)) return "image";
  // A ?v= URL is immutable (PR 317); one without it is always revalidated - leave it so.
  return searchParams.has("v") ? "static" : "network";
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    const cache = await caches.open(cacheName);
    await cache.put(request, response.clone());
  }
  return response;
}

// The cached copy at once if there is one, refreshed in the background for next time.
async function staleWhileRevalidate(event, cacheName, limit) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(event.request);
  const refresh = fetch(event.request).then(async (response) => {
    if (response.ok) {
      await cache.put(event.request, response.clone());
      if (limit) await trim(cache, limit);
    }
    return response;
  });
  if (!cached) return refresh;
  event.waitUntil(refresh.catch(() => {}));
  return cached;
}

// Oldest entries first (keys() lists them in insertion order).
async function trim(cache, limit) {
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - limit)).map((key) => cache.delete(key)));
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const route = routeFor(request.method, request.url, request.mode);
  if (route === "static") {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
  } else if (route === "image") {
    event.respondWith(staleWhileRevalidate(event, IMAGE_CACHE, IMAGE_CACHE_LIMIT));
  } else if (route === "font") {
    // Font files never change under one URL; the stylesheet listing them can.
    const fontFile = new URL(request.url).origin === "https://fonts.gstatic.com";
    event.respondWith(
      fontFile ? cacheFirst(request, FONT_CACHE) : staleWhileRevalidate(event, FONT_CACHE),
    );
  }
});
