// PR 328: the service worker, served from /service-worker.js (app/api/pwa.py) so its
// scope is the whole site. The route prepends `self.SW_CONFIG = {...}` - the version and
// the URLs to precache, both derived from the current static files - so any changed
// asset makes this script byte-different and the browser installs the new version.
"use strict";

const CONFIG = self.SW_CONFIG;

// PR 332: the queue of reading progress/activity read without a network - the same file
// the pages use (one implementation, one IndexedDB), here for Background Sync below.
importScripts(CONFIG.syncQueue);

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

// What the visitor downloaded for reading without a network (PR 330/331): written only by
// the page (offline-store.js), only ever read here.
const OFFLINE_CACHE = "wn-offline";
const CHAPTER_PATH = /^\/titles\/[^/]+\/chapters\/[^/]+\/[^/]+$/;
const OFFLINE_IMAGE_PATH = "/images/view";

// Which strategy a request gets. An allowlist: only the app's own static files and the
// webfonts are ever stored. Page navigations are "page" - fetched, never stored (HTML
// carries the signed-in user's sidebar). Proxied chapter images are "offline-image": the
// downloaded copy if there is one, never stored here. Everything else - any non-GET,
// /admin, /activity, /notifications, /settings, JSON endpoints, avatars and other
// uploads, covers hotlinked from ranobelib.me - is "network": the worker doesn't touch it.
function routeFor(method, url, mode) {
  if (method !== "GET") return "network";
  const { origin, pathname, searchParams } = new URL(url);
  if (origin !== self.location.origin) return FONT_ORIGINS.includes(origin) ? "font" : "network";
  if (mode === "navigate") return "page";
  if (pathname === OFFLINE_IMAGE_PATH) return "offline-image";
  if (!pathname.startsWith("/static/")) return "network";
  if (IMAGE_PATH.test(pathname)) return "image";
  // A ?v= URL is immutable (PR 317); one without it is always revalidated - leave it so.
  return searchParams.has("v") ? "static" : "network";
}

// PR 333: on top of the allowlist above, a second lock - whatever the route, a response
// the server marked as someone's own (Cache-Control: private / no-store, see
// app/static_assets.py) is never written to a cache here.
function storable(response) {
  if (!response.ok) return false;
  const cacheControl = (response.headers.get("Cache-Control") || "").toLowerCase();
  return !/\b(?:private|no-store)\b/.test(cacheControl);
}

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (storable(response)) {
    const cache = await caches.open(cacheName);
    await cache.put(request, response.clone());
  }
  return response;
}

// A downloaded chapter's page keeps the ?v= URLs of the day it was downloaded. Offline,
// after a deploy, that exact version is gone from the precache - the current file under
// the same path is much better than an unstyled page.
async function staticFile(request) {
  try {
    return await cacheFirst(request, STATIC_CACHE);
  } catch (error) {
    const cache = await caches.open(STATIC_CACHE);
    const current = await cache.match(request, { ignoreSearch: true });
    if (current) return current;
    throw error;
  }
}

// The cached copy at once if there is one, refreshed in the background for next time.
async function staleWhileRevalidate(event, cacheName, limit) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(event.request);
  const refresh = fetch(event.request).then(async (response) => {
    if (storable(response)) {
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

// Network-first, and never stored: a page is the signed-in user's own (sidebar, library,
// notifications). With no network: a chapter downloaded for reading offline is its kept
// page (PR 331); anything else is the precached «Нет соединения» page, which also lists
// what is downloaded and says «Глава не скачана» for a chapter that isn't. At once when
// the device already knows it's offline, rather than after a failed attempt.
async function networkPage(request) {
  if (self.navigator.onLine !== false) {
    try {
      return await fetch(request);
    } catch {
      // fall through to what's on the device
    }
  }
  return (await offlineCopy(request.url)) || (await caches.match(CONFIG.offline)) || Response.error();
}

// The downloaded copy of a chapter page, by path: the copy is of the translation picked
// at download time, so ?branch_id= doesn't pick a different one.
async function offlineCopy(url) {
  const { pathname } = new URL(url);
  if (!CHAPTER_PATH.test(pathname)) return null;
  const cache = await caches.open(OFFLINE_CACHE);
  return (await cache.match(pathname)) || null;
}

// A downloaded chapter's image from the device even online (no second trip to the
// source); otherwise the proxy, as usual. Nothing new is stored.
async function offlineImage(request) {
  const cache = await caches.open(OFFLINE_CACHE);
  return (await cache.match(request)) || fetch(request);
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const route = routeFor(request.method, request.url, request.mode);
  if (route === "page") {
    event.respondWith(networkPage(request));
  } else if (route === "static") {
    event.respondWith(staticFile(request));
  } else if (route === "offline-image") {
    event.respondWith(offlineImage(request));
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

// PR 332: where the browser has Background Sync, it wakes the worker once the network is
// back - even with no tab of the site open - to send what was read offline. Never the
// only way: pages send the queue themselves too (sync-queue.js). A rejection tells the
// browser to try again later.
self.addEventListener("sync", (event) => {
  if (event.tag !== self.syncQueue.SYNC_TAG) return;
  event.waitUntil(
    self.syncQueue.flush().then((empty) => {
      if (!empty) throw new Error("still queued");
    }),
  );
});

// A new version waits after installing (no skipWaiting on install): swapping workers
// mid-chapter could change the page under the reader. sw-register.js offers the update
// on a later page load and sends this once the visitor taps «Обновить».
self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});
