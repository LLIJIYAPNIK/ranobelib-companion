// Runs app/static/js/sync-queue.js as a page loads it, in a node:vm sandbox with a fake
// IndexedDB, a fake clock, a scriptable network and window/document events, then prints
// each scenario's outcome as JSON for tests/test_sync_queue_js.py to assert on.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 20; i += 1) await tick();
}

// Just enough IndexedDB for the queue: one object store keyed by "id"; requests succeed a
// microtask later, a transaction completes once its requests (and any they start) are done.
function fakeIndexedDB(data) {
  let upgraded = false;
  const copy = (value) => (value === undefined ? undefined : structuredClone(value));
  const transaction = () => {
    let pending = 0;
    const tx = {};
    const maybeComplete = () =>
      Promise.resolve().then(() => {
        if (pending === 0 && !tx.done) {
          tx.done = true;
          tx.oncomplete?.();
        }
      });
    const request = (work) => {
      pending += 1;
      const req = {};
      Promise.resolve().then(() => {
        req.result = work();
        req.onsuccess?.();
        pending -= 1;
        maybeComplete();
      });
      return req;
    };
    tx.objectStore = () => ({
      put: (value) => request(() => void data.set(value.id, copy(value))),
      get: (key) => request(() => copy(data.get(key))),
      getAll: () => request(() => [...data.values()].map(copy)),
      delete: (key) => request(() => void data.delete(key)),
      clear: () => request(() => void data.clear()),
    });
    maybeComplete();
    return tx;
  };
  const db = { createObjectStore: () => {}, transaction };
  return {
    open: () => {
      const req = {};
      Promise.resolve().then(() => {
        req.result = db;
        if (!upgraded) {
          upgraded = true;
          req.onupgradeneeded?.();
        }
        req.onsuccess?.();
      });
      return req;
    },
  };
}

const OK = { ok: true, status: 204, type: "basic" };
const status = (code) => ({ ok: code >= 200 && code < 300, status: code, type: "basic" });

// A page with the queue loaded. `respond(url, fields)` answers each request: a response
// object, or "down" - fetch() rejects, as with no network.
async function page({ onLine = true, indexedDB = true, stored = new Map(), deviceAccount } = {}) {
  const listeners = {};
  const on = (name, fn) => (listeners[name] ||= []).push(fn);
  const clock = { now: 1_000_000 };
  const net = { respond: () => OK };
  const requests = [];
  const syncRegistrations = [];
  let ids = 0;
  const navigator = {
    onLine,
    serviceWorker: {
      ready: Promise.resolve({ sync: { register: async (tag) => void syncRegistrations.push(tag) } }),
    },
  };
  const window = {
    addEventListener: on,
    navigator,
    crypto: { randomUUID: () => `uuid-${String((ids += 1)).padStart(4, "0")}` },
    fetch: async (url, init) => {
      const fields = Object.fromEntries(new URLSearchParams(init.body));
      requests.push({
        url,
        ...fields,
        keepalive: init.keepalive === true,
        redirect: init.redirect,
        at: clock.now,
      });
      const answer = net.respond(url, fields);
      if (answer === "down") throw new TypeError("Failed to fetch");
      return answer;
    },
  };
  if (indexedDB) window.indexedDB = fakeIndexedDB(stored);
  // PR 333: device-account.js's check, which the first send waits for.
  if (deviceAccount) window.deviceAccount = deviceAccount;
  const document = { hidden: false, addEventListener: on };
  vm.runInNewContext(source, {
    window,
    document,
    navigator,
    Date: { now: () => clock.now },
    URLSearchParams,
    Promise,
    Math,
    Uint8Array,
    TypeError,
    Error,
    Set,
  });
  const fire = (name) => (listeners[name] || []).forEach((fn) => fn({}));
  fire("DOMContentLoaded");
  await settle();
  return {
    queue: window.syncQueue,
    clock,
    net,
    requests,
    syncRegistrations,
    stored,
    document,
    navigator,
    fire,
    left: () => [...stored.values()].map(({ url, fields, at }) => ({ url, fields, at })),
  };
}

const heartbeat = { slug_url: "6712--test-novel", seconds: "30" };
const tickAt = (number, paragraph) => ({
  fields: { slug_url: "6712--test-novel", volume: "1", number, paragraph, paragraph_total: "80" },
  key: `tick:6712--test-novel:1:${number}`,
});
const sendTick = (p, number, paragraph) => {
  const { fields, key } = tickAt(number, paragraph);
  return p.queue.send("/reading-progress/tick", fields, { key });
};

const results = {};

// Online: sent at once, carrying its id, and gone from the queue once answered.
{
  const p = await page();
  await p.queue.send("/activity/heartbeat", heartbeat);
  await settle();
  results.online = { requests: p.requests, left: p.left(), syncRegistrations: p.syncRegistrations };
}

// Offline: nothing is attempted, it waits on the device; back online it goes out once,
// dated by how long it waited.
{
  const p = await page({ onLine: false });
  await p.queue.send("/activity/heartbeat", heartbeat);
  p.clock.now += 30_000;
  await p.queue.send("/activity/heartbeat", heartbeat);
  await settle();
  const whileOffline = { requests: p.requests.length, left: p.left().length, syncRegistrations: [...p.syncRegistrations] };
  p.clock.now += 60_000;
  p.navigator.onLine = true;
  p.fire("online");
  await settle();
  results.offlineThenOnline = { whileOffline, requests: p.requests, left: p.left() };
}

// The network drops mid-chapter (navigator still says online): the failed attempt stays
// queued and is sent again - with the same event id - when the page is visible again.
{
  const p = await page();
  p.net.respond = () => "down";
  await p.queue.send("/activity/heartbeat", heartbeat);
  await settle();
  p.net.respond = () => OK;
  p.document.hidden = false;
  p.fire("visibilitychange");
  await settle();
  results.droppedMidChapter = { requests: p.requests, left: p.left(), syncRegistrations: p.syncRegistrations };
}

// Ticks queued offline: only the latest of each chapter is kept, sent oldest first.
{
  const p = await page({ onLine: false });
  await sendTick(p, "5", "10");
  p.clock.now += 5_000;
  await sendTick(p, "5", "20");
  p.clock.now += 5_000;
  await sendTick(p, "6", "3");
  p.clock.now += 5_000;
  await sendTick(p, "5", "30");
  await settle();
  const queued = p.left().length;
  p.navigator.onLine = true;
  p.fire("online");
  await settle();
  results.ticksCollapse = { queued, requests: p.requests, left: p.left() };
}

// What the server says decides: signed out (303 → opaque redirect) and 422 are final -
// dropped; 503 stops the flush and keeps it and everything after it.
{
  const p = await page({ onLine: false });
  for (const seconds of ["1", "2", "3", "4"]) {
    await p.queue.send("/activity/heartbeat", { ...heartbeat, seconds });
    p.clock.now += 1;
  }
  const answers = {
    1: { ok: false, status: 0, type: "opaqueredirect" },
    2: status(422),
    3: status(503),
    4: OK,
  };
  p.net.respond = (url, fields) => answers[fields.seconds];
  p.navigator.onLine = true;
  p.fire("online");
  await settle();
  results.answers = {
    sent: p.requests.map((r) => r.seconds),
    left: p.left().map((e) => e.fields.seconds),
    syncRegistrations: p.syncRegistrations,
  };
}

// Whatever a previous page left in the queue goes out when the next page loads.
{
  const stored = new Map([
    [
      "uuid-left",
      {
        id: "uuid-left",
        nonce: "uuid-left",
        url: "/activity/heartbeat",
        fields: { ...heartbeat, event_id: "uuid-left" },
        at: 900_000,
      },
    ],
  ]);
  const p = await page({ stored });
  results.onLoad = { requests: p.requests, left: p.left() };
}

// PR 333: another account signed in here - device-account.js clears what the previous
// one left (its `ready`), and only then may the queue send anything.
{
  const stored = new Map([
    [
      "uuid-other",
      {
        id: "uuid-other",
        nonce: "uuid-other",
        url: "/activity/heartbeat",
        fields: { ...heartbeat, event_id: "uuid-other" },
        at: 900_000,
      },
    ],
  ]);
  let clear;
  const ready = new Promise((resolve) => (clear = resolve));
  const p = await page({ stored, deviceAccount: { ready } });
  const beforeReady = p.requests.length;
  await p.queue.clear();
  clear();
  await settle();
  results.accountSwitch = { beforeReady, requests: p.requests.length, left: p.left().length };
}

// A newer tick of the chapter arriving while the older one is on its way stays queued
// when the older one's answer comes back.
{
  const p = await page();
  let release;
  p.net.respond = () => new Promise((resolve) => (release = () => resolve(OK)));
  const first = sendTick(p, "5", "10");
  await settle();
  p.navigator.onLine = false;
  p.clock.now += 5_000;
  await sendTick(p, "5", "20");
  release();
  await first;
  await settle();
  results.newerKept = { left: p.left().map((e) => e.fields.paragraph) };
}

// Two flushes at once (an "online" and a "visibilitychange" together) send each event once.
{
  const p = await page({ onLine: false });
  await p.queue.send("/activity/heartbeat", heartbeat);
  await p.queue.send("/activity/heartbeat", heartbeat);
  p.navigator.onLine = true;
  p.fire("online");
  p.fire("visibilitychange");
  await settle();
  results.oneFlushAtATime = { requests: p.requests.length, left: p.left().length };
}

// No IndexedDB: still sent, just not kept.
{
  const p = await page({ indexedDB: false });
  await p.queue.send("/activity/heartbeat", heartbeat);
  await settle();
  results.noIndexedDB = { requests: p.requests.length };
}

process.stdout.write(JSON.stringify(results));
