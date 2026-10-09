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
const OFFLINE_DB = "wn-offline"; // its index (PR 336 reads it for /continue)
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
// page (PR 331), and /continue the last of those read here (PR 336); anything else is the
// precached «Нет соединения» page, which also lists what is downloaded and says «Глава не
// скачана» for a chapter that isn't. At once when the device already knows it's
// offline, rather than after a failed attempt.
//
// PR 339: a downloaded chapter, with «Сразу открывать скачанные главы из офлайн-копии» on
// (the default), doesn't wait on a bad connection either: the network still goes first -
// online, the reader is the full page, with comments, reactions and the account - but if
// it hasn't answered in COPY_AFTER_MS, or answers with a server error, the copy is shown
// instead. (The request isn't cancelled: the server still notes the chapter as opened.)
// The copy then checks itself against the site in the background (reader-copy-check.js).
const COPY_AFTER_MS = 2500;

async function networkPage(request) {
  if (self.navigator.onLine !== false) {
    const network = fetch(request);
    const copy = await quickCopy(request.url);
    try {
      return copy ? await networkOrCopy(network, copy) : await network;
    } catch {
      // fall through to what's on the device
    }
  }
  return (
    (await offlineCopy(request.url)) ||
    (await continueOffline(request.url)) ||
    (await caches.match(CONFIG.offline)) ||
    Response.error()
  );
}

// The downloaded copy of a chapter page, by path: the copy is of the translation picked
// at download time, so ?branch_id= doesn't pick a different one.
async function offlineCopy(url) {
  const { pathname } = new URL(url);
  if (!CHAPTER_PATH.test(pathname)) return null;
  const cache = await caches.open(OFFLINE_CACHE);
  return (await cache.match(pathname)) || null;
}

// PR 339: the copy a slow network may give way to - only for a downloaded chapter, and
// only with the setting on. offline-store.js keeps an entry here while it's off.
const PREFERENCES_URL = "/offline/preferences";

async function quickCopy(url) {
  const copy = await offlineCopy(url);
  if (!copy) return null;
  const preferences = await caches.match(PREFERENCES_URL, { cacheName: OFFLINE_CACHE });
  if (preferences) {
    try {
      if ((await preferences.json()).openDownloadedFromCopy === false) return null;
    } catch {
      // unreadable: the default
    }
  }
  return copy;
}

function networkOrCopy(network, copy) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(copy), COPY_AFTER_MS);
    network.then(
      (response) => {
        clearTimeout(timer);
        // 5xx (ranobelib.me blocking us, the server down) and 429: the copy is better.
        resolve(response.status >= 500 || response.status === 429 ? copy : response);
      },
      () => {
        clearTimeout(timer);
        resolve(copy);
      },
    );
  });
}

// PR 336: «Продолжить чтение» (/continue, the app icon's shortcut) is the server's
// redirect to the account's last chapter. Without a network: the chapter opened last on
// this device among the downloaded ones (offline-store.js keeps which, in its IndexedDB
// index) whose copy is still here - otherwise the offline page, as for any page.
const CONTINUE_PATH = "/continue";

async function continueOffline(url) {
  if (new URL(url).pathname !== CONTINUE_PATH) return null;
  const opened = (await downloadedTitles())
    .filter((title) => title.openedAt && title.openedChapter)
    .sort((a, b) => (a.openedAt < b.openedAt ? 1 : a.openedAt > b.openedAt ? -1 : 0));
  const cache = await caches.open(OFFLINE_CACHE);
  for (const { slug, openedChapter } of opened) {
    const path = ["titles", slug, "chapters", openedChapter.volume, openedChapter.number]
      .map(encodeURIComponent)
      .join("/");
    if (await cache.match(`/${path}`)) {
      return Response.redirect(new URL(`/${path}`, self.location.origin).href, 302);
    }
  }
  return null;
}

// The titles in the device's download index (offline-store.js) - read only, and never
// created: on a device that has downloaded nothing the open is aborted rather than
// leaving an empty database the page's own upgrade would then skip.
function downloadedTitles() {
  return new Promise((resolve) => {
    let request;
    try {
      request = self.indexedDB.open(OFFLINE_DB);
    } catch {
      resolve([]);
      return;
    }
    request.onupgradeneeded = () => request.transaction.abort();
    request.onerror = () => resolve([]);
    request.onsuccess = () => {
      const db = request.result;
      const finish = (titles) => {
        db.close(); // an open connection would block the page's next schema upgrade
        resolve(titles);
      };
      try {
        const all = db.transaction("titles").objectStore("titles").getAll();
        all.onsuccess = () => finish(all.result || []);
        all.onerror = () => finish([]);
      } catch {
        finish([]);
      }
    };
  });
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
