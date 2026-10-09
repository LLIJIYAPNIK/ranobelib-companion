// Runs the served /service-worker.js (written to a file by tests/test_service_worker.py)
// in a node:vm sandbox with fake caches, fetch and clients, then prints each scenario's
// outcome as JSON for the test to assert on. Request/Response are Node's own.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");
// PR 332: what the worker importScripts() - the real queue, whatever ?v= it asks for.
const SYNC_QUEUE = readFileSync(new URL("../../app/static/js/sync-queue.js", import.meta.url), "utf8");
const ORIGIN = "https://app.test";
const absolute = (input) => new URL(typeof input === "string" ? input : input.url, ORIGIN).href;

class FakeCache {
  constructor(fetchFn) {
    this.entries = new Map();
    this.fetchFn = fetchFn;
  }
  async match(input, options = {}) {
    let response = this.entries.get(absolute(input));
    if (!response && options.ignoreSearch) {
      const path = new URL(absolute(input)).pathname;
      for (const [url, entry] of this.entries) {
        if (new URL(url).pathname === path) {
          response = entry;
          break;
        }
      }
    }
    return response ? response.clone() : undefined;
  }
  async put(input, response) {
    this.entries.set(absolute(input), response);
  }
  async addAll(urls) {
    for (const url of urls) await this.put(url, await this.fetchFn(new Request(absolute(url))));
  }
  async delete(input) {
    return this.entries.delete(absolute(input));
  }
  async keys() {
    return [...this.entries.keys()].map((url) => new Request(url));
  }
}

function boot({ online = true, onLine = true, caches: existing = [], respond } = {}) {
  const listeners = {};
  const fetched = [];
  const calls = { skipWaiting: 0, claim: 0, imported: [] };
  const fetchFn = async (input) => {
    const url = absolute(input);
    fetched.push(url);
    if (!online) throw new TypeError("Failed to fetch");
    if (respond) return respond(url);
    return new Response(`net:${url}`, { status: 200 });
  };
  const store = new Map(existing.map((name) => [name, new FakeCache(fetchFn)]));
  const caches = {
    async open(name) {
      if (!store.has(name)) store.set(name, new FakeCache(fetchFn));
      return store.get(name);
    },
    async keys() {
      return [...store.keys()];
    },
    async delete(name) {
      return store.delete(name);
    },
    async match(input) {
      for (const cache of store.values()) {
        const hit = await cache.match(input);
        if (hit) return hit;
      }
      return undefined;
    },
  };
  const sandbox = {
    URL,
    URLSearchParams,
    Request,
    Response,
    Promise,
    Math,
    TypeError,
    caches,
    fetch: fetchFn,
    location: new URL(ORIGIN),
    navigator: { onLine },
    clients: { claim: async () => void calls.claim++ },
    skipWaiting: async () => void calls.skipWaiting++,
    addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
    importScripts: (url) => {
      calls.imported.push(url);
      vm.runInContext(SYNC_QUEUE, context);
    },
  };
  sandbox.self = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(source, context);

  async function dispatch(name, extra = {}) {
    const pending = [];
    let responded = null;
    const event = {
      ...extra,
      waitUntil: (promise) => pending.push(promise),
      respondWith: (promise) => (responded = promise),
    };
    for (const fn of listeners[name] || []) fn(event);
    const response = responded ? await responded : null;
    await Promise.all(pending);
    return response;
  }

  return { context, store, fetched, calls, dispatch };
}

// new Request() can't be built with mode "navigate"; give the worker a plain object
// with the fields it reads instead.
function navigation(url) {
  return { method: "GET", mode: "navigate", url: absolute(url) };
}

async function contents(cache) {
  return Object.fromEntries(
    await Promise.all([...cache.entries].map(async ([url, r]) => [url, await r.clone().text()])),
  );
}

async function cacheSnapshot(store) {
  const out = {};
  for (const [name, cache] of store) out[name] = Object.keys(await contents(cache));
  return out;
}

const results = {};
const config = boot().context.SW_CONFIG;
results.config = config;

// Which strategy each kind of request gets.
{
  const { context } = boot();
  const cases = [
    ["GET", "/", "navigate"],
    ["GET", "/titles/x/chapters/1/1", "navigate"],
    ["GET", "/admin", "navigate"],
    ["POST", "/login", "navigate"],
    ["POST", "/reading-progress/tick", "cors"],
    ["POST", "/activity/heartbeat", "cors"],
    ["GET", "/notifications/unread-count", "cors"],
    ["GET", "/notifications/panel", "cors"],
    ["GET", "/activity", "cors"],
    ["GET", "/admin/tables", "cors"],
    ["GET", "/settings/reading", "cors"],
    ["GET", "/downloads/status", "cors"],
    ["GET", "/titles/x/data", "cors"],
    ["GET", "/avatars/7.png", "no-cors"],
    ["GET", "/comment-attachments/a.mp4", "no-cors"],
    ["GET", "/images/download?url=x", "cors"],
    ["GET", "/images/view?url=x", "no-cors"],
    ["GET", "/static/css/app.css?v=abc", "no-cors"],
    ["GET", "/static/js/reader-hud.js?v=abc", "no-cors"],
    ["GET", "/static/js/reader-hud.js", "no-cors"],
    ["GET", "/static/icons/icon-192.png?v=abc", "no-cors"],
    ["GET", "/static/favicon/favicon-32x32.png", "no-cors"],
    ["GET", "/manifest.webmanifest", "cors"],
    ["GET", "/service-worker.js", "same-origin"],
    ["GET", "https://fonts.googleapis.com/css2?family=Manrope", "cors"],
    ["GET", "https://fonts.gstatic.com/s/manrope/v1/a.woff2", "cors"],
    ["GET", "https://cover.cdnlibs.org/uploads/cover.jpg", "no-cors"],
    ["GET", "https://ranobelib.me/uploads/ranobe/1.png", "no-cors"],
  ];
  results.routes = cases.map(([method, url, mode]) => [
    `${method} ${url} ${mode}`,
    context.routeFor(method, absolute(url), mode),
  ]);
}

// Install precaches every URL from the config into this version's cache, and doesn't
// activate itself.
{
  const sw = boot();
  await sw.dispatch("install");
  results.install = {
    caches: await cacheSnapshot(sw.store),
    skipWaiting: sw.calls.skipWaiting,
  };
}

// Activate drops older precaches only.
{
  const sw = boot({
    caches: ["wn-static-old", `wn-static-${config.version}`, "wn-images", "wn-fonts", "offline-titles"],
  });
  await sw.dispatch("activate");
  results.activate = { caches: [...sw.store.keys()], claim: sw.calls.claim };
}

// Messages: only SKIP_WAITING activates a waiting worker.
{
  const sw = boot();
  await sw.dispatch("message", { data: { type: "PING" } });
  const ignored = sw.calls.skipWaiting;
  await sw.dispatch("message", { data: { type: "SKIP_WAITING" } });
  results.messages = { ignored, skipWaiting: sw.calls.skipWaiting };
}

async function page(sw, url) {
  const before = sw.fetched.length;
  const response = await sw.dispatch("fetch", { request: navigation(url) });
  return {
    body: response && (await response.text()),
    fetched: sw.fetched.slice(before),
    caches: await cacheSnapshot(sw.store),
  };
}

// Pages: from the network and never stored; the offline page without a network - after
// a failed attempt, or at once when the device already says it's offline.
{
  const installed = boot();
  await installed.dispatch("install");
  const precache = `wn-static-${config.version}`;
  const precached = await contents(installed.store.get(precache));
  const withPrecache = async (options) => {
    const sw = boot(options);
    const cache = await sw.store.get(precache);
    for (const [url, body] of Object.entries(precached)) await cache.put(url, new Response(body));
    return sw;
  };
  results.pages = {
    online: await page(boot(), "/library"),
    offline: await page(await withPrecache({ online: false, caches: [precache] }), "/library"),
    deviceOffline: await page(
      await withPrecache({ onLine: false, caches: [precache] }),
      "/titles/x",
    ),
  };
}

// Non-page requests the worker leaves alone: no respondWith at all.
{
  const sw = boot();
  const untouched = [];
  for (const [method, url] of [
    ["POST", "/reading-progress/tick"],
    ["GET", "/notifications/unread-count"],
    ["GET", "/admin/tables"],
    ["GET", "/avatars/7.png"],
    ["GET", "https://cover.cdnlibs.org/c.jpg"],
  ]) {
    const response = await sw.dispatch("fetch", {
      request: { method, mode: "cors", url: absolute(url) },
    });
    untouched.push([`${method} ${url}`, response === null]);
  }
  results.untouched = { requests: untouched, fetched: sw.fetched, caches: [...sw.store.keys()] };
}

// Versioned static files come from the precache without the network; a miss is fetched
// once and kept.
{
  const sw = boot();
  await sw.dispatch("install");
  const precachedUrl = config.precache.find((url) => url.includes("app.css"));
  const before = sw.fetched.length;
  const hit = await sw.dispatch("fetch", {
    request: { method: "GET", mode: "no-cors", url: absolute(precachedUrl) },
  });
  const hitFetched = sw.fetched.slice(before);
  const missUrl = "/static/js/new-module.js?v=0123456789";
  const miss1 = await sw.dispatch("fetch", { request: new Request(absolute(missUrl)) });
  const miss2 = await sw.dispatch("fetch", { request: new Request(absolute(missUrl)) });
  results.static = {
    hitBody: await hit.text(),
    hitFetched,
    missBodies: [await miss1.text(), await miss2.text()],
    missFetches: sw.fetched.filter((url) => url.endsWith(missUrl)).length,
  };
}

// The image cache keeps the newest 100 entries.
{
  const sw = boot();
  for (let i = 0; i < 105; i++) {
    await sw.dispatch("fetch", { request: new Request(absolute(`/static/img/${i}.png`)) });
  }
  const keys = Object.keys(await contents(sw.store.get("wn-images")));
  results.images = { count: keys.length, first: keys[0], last: keys.at(-1) };
}

// Fonts: a failed or opaque response is passed through, not stored.
{
  const sw = boot({
    respond: (url) =>
      url.includes("broken") ? new Response("nope", { status: 404 }) : new Response(`font:${url}`),
  });
  await sw.dispatch("fetch", {
    request: new Request("https://fonts.gstatic.com/s/manrope/v1/a.woff2"),
  });
  await sw.dispatch("fetch", {
    request: new Request("https://fonts.gstatic.com/s/manrope/v1/broken.woff2"),
  });
  await sw.dispatch("fetch", {
    request: new Request("https://fonts.googleapis.com/css2?family=Manrope"),
  });
  const before = sw.fetched.length;
  await sw.dispatch("fetch", {
    request: new Request("https://fonts.gstatic.com/s/manrope/v1/a.woff2"),
  });
  results.fonts = {
    cached: Object.keys(await contents(sw.store.get("wn-fonts"))),
    refetchedFontFile: sw.fetched.length - before,
  };
}

// PR 331: reading without a network. The device's "wn-offline" cache holds a downloaded
// chapter's page under the reader URL and its proxied images; the worker reads it, never
// writes it.
{
  const precache = `wn-static-${config.version}`;
  const COPY = "/titles/6712--test-novel/chapters/1/3";
  const IMAGE = "/images/view?url=https%3A%2F%2Franobelib.me%2Fa.png";
  const seed = async (sw) => {
    const offline = await sw.store.get("wn-offline");
    await offline.put(COPY, new Response("copy:1/3"));
    await offline.put(IMAGE, new Response("image:a"));
    const statics = await sw.store.get(precache);
    await statics.put("/offline", new Response("offline-page"));
    await statics.put("/static/css/app.css?v=new0000000", new Response("css:new"));
  };
  const make = async (options) => {
    const sw = boot({ ...options, caches: ["wn-offline", precache] });
    await seed(sw);
    return sw;
  };
  const navigate = async (sw, url) => {
    const before = sw.fetched.length;
    const response = await sw.dispatch("fetch", { request: navigation(url) });
    return { body: await response.text(), fetched: sw.fetched.slice(before) };
  };
  const get = async (sw, url, mode = "no-cors") => {
    const before = sw.fetched.length;
    const response = await sw.dispatch("fetch", { request: { method: "GET", mode, url: absolute(url) } });
    return {
      body: response ? await response.text() : null,
      answered: response !== null,
      fetched: sw.fetched.slice(before),
    };
  };

  const online = await make({});
  const offline = await make({ online: false });
  const deviceOffline = await make({ onLine: false });
  results.offlineReading = {
    online: {
      // Online the reader is always the network's - the copy is only the fallback.
      chapter: await navigate(online, COPY),
      image: await get(online, IMAGE),
      otherImage: await get(online, "/images/view?url=https%3A%2F%2Franobelib.me%2Fb.png"),
      oldCss: await get(online, "/static/css/app.css?v=old0000000"),
    },
    offline: {
      chapter: await navigate(offline, COPY),
      chapterWithBranch: await navigate(offline, `${COPY}?branch_id=7`),
      notDownloaded: await navigate(offline, "/titles/6712--test-novel/chapters/1/4"),
      titlePage: await navigate(offline, "/titles/6712--test-novel"),
      otherTitle: await navigate(offline, "/titles/1--other/chapters/1/3"),
      image: await get(offline, IMAGE),
      oldCss: await get(offline, "/static/css/app.css?v=old0000000"),
    },
    deviceOffline: {
      chapter: await navigate(deviceOffline, COPY),
    },
    // Nothing the worker did above added a single entry to the device's copy.
    offlineCacheAfter: [
      Object.keys(await contents(online.store.get("wn-offline"))).length,
      Object.keys(await contents(offline.store.get("wn-offline"))).length,
    ],
  };
}

// PR 332: Background Sync - the worker sends what pages queued while offline.
{
  const queued = (overrides = {}) => ({
    id: "event-0001",
    nonce: "event-0001",
    url: "/activity/heartbeat",
    fields: { slug_url: "6712--test-novel", seconds: "30", event_id: "event-0001" },
    at: 0,
    ...overrides,
  });
  const memoryStore = (events) => {
    const map = new Map(events.map((event) => [event.id, event]));
    return {
      map,
      put: async (event) => void map.set(event.id, event),
      all: async () => [...map.values()],
      remove: async (event) => {
        if (map.get(event.id)?.nonce === event.nonce) map.delete(event.id);
      },
    };
  };
  const sync = async (options, tag = "wn-sync") => {
    const sw = boot(options);
    const store = memoryStore([queued(), queued({ id: "event-0002", nonce: "event-0002", at: 5 })]);
    sw.context.syncQueue.store = store;
    let rejected = false;
    try {
      await sw.dispatch("sync", { tag });
    } catch {
      rejected = true;
    }
    return { rejected, fetched: sw.fetched, left: [...store.map.keys()], imported: sw.calls.imported };
  };
  results.backgroundSync = {
    online: await sync({ respond: () => new Response(null, { status: 204 }) }),
    offline: await sync({ online: false }),
    otherTag: await sync({}, "something-else"),
  };
}

process.stdout.write(JSON.stringify(results));
