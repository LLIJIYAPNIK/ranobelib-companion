// PR 332: the device's outbox for what a reading page reports to the server - POST
// /reading-progress/tick (reading-progress-tick.js) and POST /activity/heartbeat
// (activity-heartbeat.js). Read offline (a downloaded chapter, PR 331) or on a network that
// drops, those used to be lost; now every event is written to IndexedDB ("wn-sync") first,
// sent right away when there's a network, and removed only once the server has answered.
// What's left is sent later, one request at a time and oldest first:
//   - on "online", when a page becomes visible again, and on every page load;
//   - by the service worker on Background Sync (service-worker.js imports this file) where
//     the browser has it - never relied on: iOS doesn't.
//
// Sending twice is harmless by design, so nothing here has to be exactly-once (an answer
// lost on the way back, two tabs, a tab and the worker): a heartbeat carries its event id
// and the server ignores a repeat (activity_events.event_id); a tick is a position, and
// the server keeps «кто дальше, тот и победил» (PR 287) for late ones - each event says how
// long ago it happened (age_ms, on this device's own clock), so the server dates it right.
// Queued ticks of one chapter collapse into the latest (its `key`): only that one matters.
//
// An answer decides an event's fate: 2xx - delivered; a redirect (303 to /login: nobody
// signed in on this device now) or another 4xx (422: e.g. older than the server accepts) -
// it will never be accepted, dropped; a failed fetch, 408, 429 or 5xx - kept for later.
(() => {
  const scope = typeof window === "undefined" ? self : window;
  if (scope.syncQueue) return;

  const DB_NAME = "wn-sync";
  const STORE = "events";
  const SYNC_TAG = "wn-sync";

  function outcomeOf(response) {
    if (response.type === "opaqueredirect") return "drop";
    if (response.ok) return "done";
    const { status } = response;
    return status === 408 || status === 429 || status >= 500 ? "retry" : "drop";
  }

  function newId() {
    if (scope.crypto?.randomUUID) return scope.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    if (scope.crypto?.getRandomValues) scope.crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  // `store`: put(event), all(), remove(event) - promises. `onPending`: something stayed
  // queued (offline, or the server said "later") - the page asks for Background Sync.
  class SyncQueue {
    constructor({ store, fetch, now, online, onPending = () => {} }) {
      this.store = store;
      this.fetch = fetch;
      this.now = now;
      this.online = online;
      this.onPending = onPending;
      this.inFlight = new Set(); // nonces being sent by this page right now
      this._flushing = null;
    }

    // The request starts synchronously (keepalive: it survives a pagehide), alongside the
    // write to the queue; the event leaves the queue once the answer is final.
    send(url, fields, { key } = {}) {
      const nonce = newId();
      const event = {
        id: key || nonce,
        nonce,
        url,
        fields: key ? { ...fields } : { ...fields, event_id: nonce },
        at: this.now(),
      };
      const stored = this.store.put(event).then(
        () => true,
        () => false,
      );
      if (!this.online()) return stored.then((queued) => queued && this.onPending());
      this.inFlight.add(nonce);
      const attempt = this._post(event);
      return Promise.all([stored, attempt])
        .then(([queued, outcome]) => {
          this.inFlight.delete(nonce);
          if (!queued) return undefined;
          if (outcome === "retry") return this.onPending();
          return this.store.remove(event);
        })
        .catch(() => {}); // left in the queue: the next flush sends it again
    }

    // Everything queued, oldest first, one at a time; stops at the first "later" (still
    // no network, or the server is pushing back) and keeps the rest. Resolves true once
    // the queue is empty. Never two drains at once in one page.
    flush() {
      this._flushing ||= this._drain()
        .catch(() => false)
        .finally(() => {
          this._flushing = null;
        });
      return this._flushing;
    }

    async _drain() {
      const events = await this.store.all();
      events.sort((a, b) => a.at - b.at);
      for (const event of events) {
        if (this.inFlight.has(event.nonce)) continue;
        this.inFlight.add(event.nonce);
        let outcome;
        try {
          outcome = await this._post(event);
        } finally {
          this.inFlight.delete(event.nonce);
        }
        if (outcome === "retry") {
          this.onPending();
          return false;
        }
        await this.store.remove(event);
      }
      return true;
    }

    _post(event) {
      const age = Math.max(0, this.now() - event.at);
      return this.fetch(event.url, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ ...event.fields, age_ms: String(age) }),
        keepalive: true,
        credentials: "same-origin",
        redirect: "manual",
      }).then(outcomeOf, () => "retry");
    }
  }

  function idbStore(indexedDB) {
    let opening = null;
    const open = () => {
      opening ||= new Promise((resolve, reject) => {
        const request = indexedDB.open(DB_NAME, 1);
        request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "id" });
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      opening.catch(() => {
        opening = null; // try again next time (e.g. storage was briefly unavailable)
      });
      return opening;
    };
    const transaction = async (mode, work) => {
      const tx = (await open()).transaction(STORE, mode);
      const result = work(tx.objectStore(STORE));
      await new Promise((resolve, reject) => {
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error);
      });
      return result;
    };
    return {
      put: (event) => transaction("readwrite", (store) => void store.put(event)),
      all: async () => {
        let request;
        await transaction("readonly", (store) => {
          request = store.getAll();
        });
        return request.result;
      },
      // Only the copy that was sent: a newer event under the same key stays queued.
      remove: (event) =>
        transaction("readwrite", (store) => {
          const request = store.get(event.id);
          request.onsuccess = () => {
            if (request.result?.nonce === event.nonce) store.delete(event.id);
          };
        }),
    };
  }

  // No IndexedDB (some private modes): just send, as before this PR.
  const noStore = {
    put: () => Promise.reject(new Error("no IndexedDB")),
    all: async () => [],
    remove: async () => {},
  };

  const inPage = typeof window !== "undefined";
  function requestBackgroundSync() {
    if (!inPage || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.ready
      .then((registration) => registration.sync?.register(SYNC_TAG))
      .catch(() => {});
  }

  const queue = new SyncQueue({
    store: scope.indexedDB ? idbStore(scope.indexedDB) : noStore,
    fetch: (...args) => scope.fetch(...args),
    now: () => Date.now(),
    online: () => scope.navigator?.onLine !== false,
    onPending: requestBackgroundSync,
  });
  queue.SYNC_TAG = SYNC_TAG;
  scope.syncQueue = queue;
  scope.SyncQueue = SyncQueue; // for tests (tests/js/sync_queue_harness.mjs)

  if (!inPage) return; // the service worker flushes on "sync" (service-worker.js)
  window.addEventListener("online", () => queue.flush());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) queue.flush();
  });
  queue.flush();
})();
