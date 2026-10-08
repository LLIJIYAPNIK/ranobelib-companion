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
