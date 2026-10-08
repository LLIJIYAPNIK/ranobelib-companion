// Runs app/static/js/offline-queue.js and offline-store.js in node:vm sandboxes and prints
// each scenario's outcome as JSON for tests/test_offline_download.py: the queue with a
// fake `download` whose chapters finish only when the scenario says so (to catch two at
// once), and the IndexedDB schema migrations against a fake upgrade-time database.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const queueSource = readFileSync(process.argv[2], "utf8");
const storeSource = readFileSync(process.argv[3], "utf8");

function loadQueue() {
  const window = {};
  vm.runInNewContext(queueSource, { window, AbortController, DOMException, Error, Promise });
  return window.OfflineQueue;
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
  for (let i = 0; i < 20; i += 1) await tick();
}

const items = (n) => Array.from({ length: n }, (_, i) => ({ volume: "1", number: String(i + 1) }));

// A download whose every call waits for the scenario to finish (or fail) it.
function controlledDownload() {
  const log = { started: [], inFlight: 0, maxInFlight: 0, aborted: [] };
  const pending = [];
  function download(item, signal) {
    log.started.push(item.number);
    log.inFlight += 1;
    log.maxInFlight = Math.max(log.maxInFlight, log.inFlight);
    return new Promise((resolve, reject) => {
      // Settles once: an abort after the chapter already finished or failed is a no-op,
      // as with a real fetch - and must not count the chapter out of flight twice.
      let settled = false;
      const settle = (fn) => (value) => {
        if (settled) return;
        settled = true;
        log.inFlight -= 1;
        fn(value);
      };
      const entry = { item, resolve: settle(resolve), reject: settle(reject) };
      // Like fetch(), an aborted call rejects a moment later, not synchronously.
      signal.addEventListener("abort", () => {
        if (settled) return;
        log.aborted.push(item.number);
        setImmediate(() => entry.reject(new DOMException("Aborted", "AbortError")));
      });
      pending.push(entry);
    });
  }
  const next = () => pending.shift();
  return { download, log, next };
}

function snapshot(queue, log) {
  return {
    state: queue.state,
    pauseReason: queue.pauseReason,
    position: queue.position,
    completed: queue.completed,
    bytes: queue.bytes,
    failed: queue.failed.map((entry) => [entry.item.number, entry.message]),
    started: [...log.started],
    maxInFlight: log.maxInFlight,
    aborted: [...log.aborted],
  };
}

const OfflineQueue = loadQueue();
const results = {};

// One after another: the second chapter starts only after the first is done.
{
  const { download, log, next } = controlledDownload();
  const queue = new OfflineQueue(items(3), { download });
  queue.start();
  await settle();
  const afterStart = [...log.started];
  for (let i = 0; i < 3; i += 1) {
    next().resolve(100);
    await settle();
  }
  results.sequential = { afterStart, ...snapshot(queue, log) };
}

// 429 / 503 / no space / no network: paused in place; «Продолжить» redoes that chapter.
for (const [name, error] of [
  ["rateLimit", () => new OfflineQueue.HttpError(429, "slow")],
  ["blocked", () => new OfflineQueue.HttpError(503, "blocked")],
  ["quota", () => new DOMException("full", "QuotaExceededError")],
  ["offline", () => new TypeError("Failed to fetch")],
]) {
  const { download, log, next } = controlledDownload();
  const queue = new OfflineQueue(items(3), { download });
  queue.start();
  await settle();
  next().resolve(10);
  await settle();
  next().reject(error());
  await settle();
  const paused = snapshot(queue, log);
  queue.resume();
  await settle();
  next().resolve(10);
  await settle();
  next().resolve(10);
  await settle();
  results[name] = { paused, after: snapshot(queue, log) };
}

// Any other failure is that chapter's alone: listed, skipped; «Повторить» runs just it.
{
  const { download, log, next } = controlledDownload();
  const queue = new OfflineQueue(items(3), { download });
  queue.start();
  await settle();
  next().reject(new OfflineQueue.HttpError(404, "Глава не найдена"));
  await settle();
  next().resolve(5);
  await settle();
  next().resolve(5);
  await settle();
  const done = snapshot(queue, log);
  queue.retryFailed();
  await settle();
  next().resolve(5);
  await settle();
  results.failedThenRetried = { done, after: snapshot(queue, log) };
}

// The visitor pauses mid-chapter and resumes at once, before the aborted request has
// settled: still never two at once, and the interrupted chapter is fetched again.
{
  const { download, log, next } = controlledDownload();
  const queue = new OfflineQueue(items(2), { download });
  queue.start();
  await settle();
  queue.pause();
  queue.resume();
  await settle();
  next(); // the aborted first attempt, already rejected by its signal
  next().resolve(7);
  await settle();
  next().resolve(7);
  await settle();
  results.pauseResume = snapshot(queue, log);
}

// Cancel: the request in flight is aborted and nothing more is fetched.
{
  const { download, log, next } = controlledDownload();
  const queue = new OfflineQueue(items(3), { download });
  queue.start();
  await settle();
  next().resolve(1);
  await settle();
  queue.cancel();
  await settle();
  const cancelled = snapshot(queue, log);
  queue.start();
  await settle();
  results.cancel = { cancelled, restarted: snapshot(queue, log) };
}

// Schema migrations, against a fake upgrade-time database.
function fakeDb(existing = {}) {
  const stores = { ...existing };
  return {
    stores,
    createObjectStore(name, options) {
      if (stores[name]) throw new Error(`ConstraintError: ${name} exists`);
      const store = { options, indexes: {} };
      store.createIndex = (indexName, keyPath) => {
        store.indexes[indexName] = keyPath;
      };
      stores[name] = store;
      return store;
    },
  };
}

{
  const window = {};
  vm.runInNewContext(storeSource, { window, navigator: {}, Blob, Response, Promise });
  const store = window.offlineStore;
  const fresh = fakeDb();
  store.upgrade(fresh, 0);
  const current = fakeDb(fresh.stores);
  store.upgrade(current, store.DB_VERSION);
  const describe = (db) =>
    Object.fromEntries(
      Object.entries(db.stores).map(([name, s]) => [name, { keyPath: s.options.keyPath, indexes: s.indexes }]),
    );
  results.migrations = {
    version: store.DB_VERSION,
    steps: store.MIGRATIONS.length,
    fresh: describe(fresh),
    reopenedUnchanged: describe(current),
    supportedWithoutApis: store.supported(),
    chapterUrl: store.chapterUrl("6712--test-novel", "1", "2.5"),
    coverUrl: store.coverUrl("https://cover.cdnlibs.org/a b.jpg"),
  };
}

process.stdout.write(JSON.stringify(results));
