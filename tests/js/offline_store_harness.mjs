// Runs app/static/js/offline-store.js in a node:vm sandbox over an in-memory IndexedDB and
// Cache Storage and prints each scenario's outcome as JSON for
// tests/test_offline_store_js.py (PR 335): sizes per title, the limit, what's "read",
// cleaning it up, deleting a title, the last-opened date and the offline settings.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const storeSource = readFileSync(process.argv[2], "utf8");

// --- a small IndexedDB: object stores with a keyPath, indexes, transactions -----------
// Requests settle on a later macrotask; a transaction completes once none of its
// requests is pending after the microtasks that follow the last one - so a get awaited
// inside a transaction can still be followed by a put in it, as in a browser.
function fakeIndexedDB() {
  const databases = new Map();
  const keyOf = (keyPath, value) =>
    JSON.stringify(Array.isArray(keyPath) ? keyPath.map((k) => value[k]) : value[keyPath]);
  const clone = (value) => (value === undefined ? undefined : structuredClone(value));

  function makeTransaction(db) {
    let pending = 0;
    const tx = { oncomplete: null, onerror: null, onabort: null };
    const check = () =>
      setTimeout(() => {
        if (pending === 0 && !tx.done) {
          tx.done = true;
          tx.oncomplete?.();
        }
      }, 0);
    const request = (compute) => {
      const req = { onsuccess: null, onerror: null, result: undefined };
      pending += 1;
      setImmediate(() => {
        req.result = compute();
        pending -= 1;
        req.onsuccess?.();
        check();
      });
      return req;
    };
    tx.objectStore = (name) => {
      const store = db.stores.get(name);
      return {
        get: (key) => request(() => clone(store.rows.get(JSON.stringify(key)))),
        getAll: () => request(() => [...store.rows.values()].map(clone)),
        put: (value) => request(() => void store.rows.set(keyOf(store.keyPath, value), clone(value))),
        delete: (key) => request(() => void store.rows.delete(JSON.stringify(key))),
        clear: () => request(() => void store.rows.clear()),
        index: (indexName) => ({
          getAll: (value) =>
            request(() =>
              [...store.rows.values()].filter((row) => row[store.indexes[indexName]] === value).map(clone),
            ),
        }),
      };
    };
    check();
    return tx;
  }

  return {
    open(name, version) {
      const req = { onupgradeneeded: null, onsuccess: null, onerror: null, result: undefined };
      setImmediate(() => {
        let db = databases.get(name);
        const oldVersion = db?.version || 0;
        if (!db) {
          db = { version: 0, stores: new Map() };
          databases.set(name, db);
        }
        db.createObjectStore = (storeName, { keyPath }) => {
          const store = { keyPath, rows: new Map(), indexes: {} };
          db.stores.set(storeName, store);
          return { createIndex: (indexName, path) => (store.indexes[indexName] = path) };
        };
        db.transaction = () => makeTransaction(db);
        req.result = db;
        if (oldVersion < version) {
          req.onupgradeneeded?.({ oldVersion });
          db.version = version;
        }
        req.onsuccess?.();
      });
      return req;
    },
  };
}

function fakeCaches() {
  const caches = new Map();
  return {
    entries: caches,
    async open(name) {
      if (!caches.has(name)) caches.set(name, new Map());
      const cache = caches.get(name);
      return {
        put: async (url, response) => void cache.set(url, response),
        match: async (url) => cache.get(url),
        delete: async (url) => cache.delete(url),
      };
    },
    delete: async (name) => caches.delete(name),
    // PR 339: the preference entry is looked up in this one cache only.
    match: async (url, { cacheName } = {}) => caches.get(cacheName)?.get(url),
  };
}

function sandbox() {
  const storage = new Map();
  const localStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  const indexedDB = fakeIndexedDB();
  const caches = fakeCaches();
  const window = { indexedDB, caches };
  const context = {
    window,
    indexedDB,
    caches,
    localStorage,
    navigator: {},
    Blob,
    Response,
    Promise,
    Date,
    Math,
    Number,
    String,
    Set,
    Map,
    JSON,
    Object,
    Array,
    encodeURIComponent,
  };
  vm.runInNewContext(storeSource, context);
  return { store: window.offlineStore, localStorage, caches, indexedDB };
}

const SLUG = "6712--test-novel";
const OTHER = "9001--other-novel";
const TOC = Array.from({ length: 10 }, (_, i) => ["1", String(i + 1)]);
const KB = 1024;

// Chapters 1-10 of SLUG, 1000 + n bytes of page each; chapter n has image n, and
// chapters 2 and 9 share /img/shared. OTHER has chapter 1, which also uses image 3.
// SLUG's cover is in the cache, as saveCover() leaves it.
async function seed(store, caches) {
  await (await caches.open("wn-offline")).put("/images/view?url=cover", new Response("c"));
  for (const n of TOC.map(([, number]) => number)) {
    const images = [{ url: `/img/${n}`, response: new Response("x".repeat(KB)) }];
    if (n === "2" || n === "9") images.push({ url: "/img/shared", response: new Response("y".repeat(KB)) });
    await store.saveChapter(
      { name: "Повелитель тайн", cover: "/images/view?url=cover", toc: TOC },
      { slug_url: SLUG, volume: "1", number: n, name: `Глава ${n}`, branch_id: null },
      "p".repeat(1000 + Number(n)),
      images,
    );
  }
  await store.saveChapter(
    { name: "Другой", cover: null, toc: [["1", "1"]] },
    { slug_url: OTHER, volume: "1", number: "1", name: "Глава 1", branch_id: null },
    "q".repeat(500),
    [{ url: "/img/3", response: new Response("z".repeat(KB)) }],
  );
}

const cached = (caches) => [...(caches.entries.get("wn-offline")?.keys() || [])].sort();
const numbers = async (store, slug) =>
  (await store.chaptersOf(slug)).map((c) => Number(c.number)).sort((a, b) => a - b);

const results = {};

// Sizes: per title and in all, and the limit against them.
{
  const { store, localStorage, caches, indexedDB } = sandbox();
  await seed(store, caches);
  const titles = await store.listTitles();
  results.sizes = Object.fromEntries(titles.map((t) => [t.slug, { chapters: t.chapters, bytes: t.bytes }]));
  results.limitOff = await store.limitState();
  localStorage.setItem("offlineSettings", JSON.stringify({ limitMb: 100 }));
  results.limit100 = await store.limitState();
  // A tiny limit to see "reached": 0.05 MB isn't a choice - garbage reads as no limit.
  localStorage.setItem("offlineSettings", JSON.stringify({ limitMb: 0.05 }));
  results.limitGarbage = await store.limitState();
  // One more chapter of 100 MB puts the downloads past a 100 MB limit.
  const open = indexedDB.open("wn-offline", store.DB_VERSION);
  const db = await new Promise((resolve) => (open.onsuccess = () => resolve(open.result)));
  const put = db.transaction(["chapters"], "readwrite").objectStore("chapters").put({
    slug: OTHER,
    volume: "1",
    number: "2",
    bytes: 100 * KB * KB,
    images: [],
  });
  await new Promise((resolve) => (put.onsuccess = resolve));
  localStorage.setItem("offlineSettings", JSON.stringify({ limitMb: 100 }));
  results.limit100Reached = await store.limitState();
}

// What's "read": behind the current chapter, minus `keep`, never the current one or after.
{
  const { store } = sandbox();
  results.readKeys = {
    current6keep0: [...store.readKeys(TOC, { volume: "1", number: "6" }, 0)],
    current6keep2: [...store.readKeys(TOC, { volume: "1", number: "6" }, 2)],
    current2keep5: [...store.readKeys(TOC, { volume: "1", number: "2" }, 5)],
    first: [...store.readKeys(TOC, { volume: "1", number: "1" }, 0)],
    notInToc: [...store.readKeys(TOC, { volume: "2", number: "1" }, 0)],
    noToc: [...store.readKeys(null, { volume: "1", number: "6" }, 0)],
  };
}

// Auto-cleanup at chapter 6 keeping 2 behind: 1-3 go, 4-10 stay; images used only by
// the chapters that went go, the one chapter 9 shares stays, as does chapter 3's image
// the other title uses; the title, its cover and the other title stay.
{
  const { store, caches } = sandbox();
  await seed(store, caches);
  const before = cached(caches);
  const freed = await store.deleteRead(SLUG, 2, { volume: "1", number: "6" });
  const after = cached(caches);
  const titles = await store.listTitles();
  results.cleanup = {
    freed,
    left: await numbers(store, SLUG),
    other: await numbers(store, OTHER),
    removedFromCache: before.filter((url) => !after.includes(url)),
    titleKept: titles.some((t) => t.slug === SLUG),
    coverKept: after.includes("/images/view?url=cover"),
  };
}

// «Удалить прочитанное»: the chapter opened last (readerLastChapter) is the current one.
{
  const { store, localStorage, caches } = sandbox();
  await seed(store, caches);
  results.noneOpened = await store.deleteRead(SLUG, 0);
  localStorage.setItem(`readerLastChapter:${SLUG}`, "1--4");
  const read = (await store.readChapters(SLUG, 0)).map((c) => Number(c.number)).sort((a, b) => a - b);
  const freed = await store.deleteRead(SLUG, 0);
  results.lastOpened = { read, freed, left: await numbers(store, SLUG) };
  results.lastOpenedAgain = await store.deleteRead(SLUG, 0);
}

// deleteTitle (now deleteChapters with every chapter): the title and its cover go too,
// and an image the other title uses stays.
{
  const { store, caches } = sandbox();
  await seed(store, caches);
  await store.deleteTitle(SLUG);
  results.deleteTitle = {
    titles: (await store.listTitles()).map((t) => t.slug),
    cache: cached(caches),
  };
}

// The last-opened date: only for a downloaded title; forgotten on request.
{
  const { store, caches } = sandbox();
  await seed(store, caches);
  await store.touchTitle(SLUG, new Date("2026-10-01T10:00:00Z"));
  await store.touchTitle("404--not-downloaded", new Date("2026-10-01T10:00:00Z"));
  const opened = Object.fromEntries((await store.listTitles()).map((t) => [t.slug, t.openedAt ?? null]));
  await store.forgetOpened();
  const forgotten = Object.fromEntries((await store.listTitles()).map((t) => [t.slug, t.openedAt ?? null]));
  results.opened = { opened, forgotten, titles: (await store.listTitles()).length };
}

// PR 336: which chapter was opened, for /continue offline - kept with the date, replaced
// by the next one, forgotten with it.
{
  const { store, caches } = sandbox();
  await seed(store, caches);
  const chapterOf = async () =>
    Object.fromEntries((await store.listTitles()).map((t) => [t.slug, t.openedChapter ?? null]));
  await store.touchTitle(SLUG, new Date("2026-10-01T10:00:00Z"), { volume: 1, number: "4" });
  const first = await chapterOf();
  await store.touchTitle(SLUG, new Date("2026-10-02T10:00:00Z"), { volume: "1", number: "5" });
  const second = await chapterOf();
  await store.forgetOpened();
  results.openedChapter = { first, second, forgotten: await chapterOf() };
}

// The offline settings: defaults, choices only, merged saves.
{
  const { store, localStorage } = sandbox();
  const defaults = store.settings();
  store.saveSettings({ cleanBehind: 10 });
  store.saveSettings({ limitMb: 250 });
  const saved = store.settings();
  localStorage.setItem("offlineSettings", JSON.stringify({ cleanBehind: 7, limitMb: "500" }));
  const coerced = store.settings();
  localStorage.setItem("offlineSettings", "{not json");
  results.settings = { defaults, saved, coerced, garbage: store.settings() };
}

// PR 339: downloading another chapter keeps when the title was opened and which chapter
// (it used to replace the whole title record).
{
  const { store, caches } = sandbox();
  await seed(store, caches);
  await store.touchTitle(SLUG, new Date("2026-10-01T10:00:00Z"), { volume: "1", number: "4" });
  await store.saveChapter(
    { name: "Повелитель тайн", cover: "/images/view?url=cover", toc: TOC },
    { slug_url: SLUG, volume: "1", number: "11", name: "Глава 11", branch_id: null },
    "p",
    [],
  );
  const title = (await store.listTitles()).find((t) => t.slug === SLUG);
  results.saveKeepsOpened = {
    openedAt: title.openedAt ?? null,
    openedChapter: title.openedChapter ?? null,
    chapters: title.chapters,
  };
}

// PR 339: after «Обновить копию», the old copy's images no chapter uses any more.
{
  const { store, caches } = sandbox();
  await seed(store, caches);
  await store.dropUnusedImages(["/img/gone", "/img/shared", "/img/3"]);
  // /img/gone isn't in any chapter: dropped; /img/shared (chapters 2, 9) and /img/3 stay.
  await (await caches.open("wn-offline")).put("/img/gone", new Response("g"));
  const before = cached(caches).includes("/img/gone");
  await store.dropUnusedImages(["/img/gone", "/img/shared", "/img/3"]);
  results.dropUnused = { before, after: cached(caches) };
}

// PR 339: «Сразу открывать скачанные главы из офлайн-копии» handed to the service
// worker - an entry only while it's off.
{
  const { store, localStorage, caches } = sandbox();
  const entry = async () => {
    const response = await caches.entries.get("wn-offline")?.get(store.PREFERENCES_URL);
    return response ? JSON.parse(await response.clone().text()) : null;
  };
  const steps = {};
  steps.defaultOn = store.openFromCopy();
  await store.syncPreferences();
  steps.nothingWritten = caches.entries.has("wn-offline");
  localStorage.setItem("readerSettings", JSON.stringify({ openDownloadedFromCopy: false }));
  steps.readOff = store.openFromCopy();
  await store.syncPreferences();
  steps.off = await entry();
  await store.syncPreferences(false); // already there: left as is
  steps.offAgain = await entry();
  await store.syncPreferences(true);
  steps.onAgain = await entry();
  localStorage.setItem("readerSettings", "{broken");
  steps.garbage = store.openFromCopy();
  results.preferences = steps;
}

process.stdout.write(JSON.stringify(results));
