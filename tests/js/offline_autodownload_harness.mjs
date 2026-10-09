// Runs app/static/js/offline-autodownload.js (with the real offline-queue.js under it) in
// a node:vm sandbox: fake localStorage, connection, downloads store, manifest and chapter
// downloads. Prints each scenario's outcome as JSON for tests/test_offline_autodownload_js.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const [queueSource, autoSource] = process.argv.slice(2).map((path) => readFileSync(path, "utf8"));

const SLUG = "6712--test-novel";
const BRANCHES = [{ branch_id: 101 }, { branch_id: 102 }];
// Chapters 1-12 of volume 1; chapter 8 has two translations.
const CHAPTERS = Array.from({ length: 12 }, (_, i) => ({
  volume: "1",
  number: String(i + 1),
  name: `Глава ${i + 1}`,
  branches: i + 1 === 8 ? BRANCHES : [{ branch_id: 101 }],
}));
const MANIFEST = { slug_url: SLUG, volumes: [{ number: "1", chapters: CHAPTERS }] };

function sandbox() {
  const storage = new Map();
  const localStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  const window = {};
  const context = {
    window,
    document: { querySelector: () => null, addEventListener: () => {} },
    navigator: {},
    localStorage,
    Promise,
    Math,
    Number,
    String,
    Set,
    Map,
    JSON,
    Error,
    TypeError,
    DOMException,
    AbortController,
    encodeURIComponent,
    setTimeout,
  };
  vm.runInNewContext(queueSource, context);
  vm.runInNewContext(autoSource, context);
  return { window, localStorage };
}

async function scenario({
  next = "3",
  anyNetwork,
  connection = { type: "wifi" },
  current = "5",
  saved = ["1--1", "1--2", "1--3", "1--4", "1--5"],
  inSet = true,
  variant = null,
  toc = CHAPTERS.slice(0, 6).map((c) => [c.volume, c.number]),
  nearlyFull = false,
  fail = {},
  cooldownUntil,
  now = 1_000_000,
} = {}) {
  const { window, localStorage } = sandbox();
  localStorage.setItem("readerSettings", JSON.stringify({ autoDownloadNext: next, autoDownloadAnyNetwork: anyNetwork }));
  if (cooldownUntil) localStorage.setItem("offlineAutoPausedUntil", String(cooldownUntil));
  const savedSet = new Set(saved);
  const fetched = [];
  const downloads = [];
  let inFlight = 0;
  let maxInFlight = 0;
  const store = {
    supported: () => true,
    listTitles: async () =>
      inSet ? [{ slug: SLUG, name: "Повелитель тайн", cover: null, toc, translationVariant: variant }] : [],
    storageState: async () => ({ percent: nearlyFull ? 86 : 10, nearlyFull }),
    savedKeys: async () => new Set(savedSet),
  };
  const Queue = class extends window.OfflineQueue {};
  Queue.downloadChapter = async (title, item) => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    downloads.push({ number: item.number, branchId: item.branchId, tocLength: title.toc.length });
    await new Promise((resolve) => setTimeout(resolve, 5));
    inFlight -= 1;
    const failure = fail[item.number];
    if (failure === "quota") throw new DOMException("full", "QuotaExceededError");
    if (failure) throw new window.OfflineQueue.HttpError(failure, "nope");
    savedSet.add(`${item.volume}--${item.number}`);
    return 1000;
  };
  const fetch = async (url) => {
    fetched.push(url);
    return { ok: true, status: 200, json: async () => MANIFEST };
  };
  const result = await window.offlineAuto.runFor(
    { slug: SLUG, volume: "1", number: current },
    { store, OfflineQueue: Queue, fetch, connection, now: () => now },
  );
  return {
    result,
    fetched,
    downloads,
    maxInFlight,
    cooldownUntil: Number(localStorage.getItem("offlineAutoPausedUntil") || 0) || null,
  };
}

const results = {
  off: await scenario({ next: "0" }),
  wifi: await scenario(),
  ethernet: await scenario({ connection: { type: "ethernet" } }),
  saveData: await scenario({ connection: { type: "wifi", saveData: true } }),
  cellular: await scenario({ connection: { type: "cellular" } }),
  unknownNotAllowed: await scenario({ connection: null }),
  unknownAllowed: await scenario({ connection: null, anyNetwork: true }),
  notInSet: await scenario({ inSet: false }),
  alreadyAhead: await scenario({ saved: ["1--5", "1--6", "1--7", "1--8"], toc: CHAPTERS.map((c) => [c.volume, c.number]) }),
  partlyAhead: await scenario({ saved: ["1--5", "1--6"] }),
  ten: await scenario({ next: "10", current: "1", saved: ["1--1"] }),
  variantChosen: await scenario({ current: "6", saved: ["1--6"], variant: 1 }),
  variantUnknown: await scenario({ current: "6", saved: ["1--6"] }),
  rateLimited: await scenario({ fail: { 7: 429 } }),
  blocked: await scenario({ fail: { 6: 503 } }),
  quota: await scenario({ fail: { 6: "quota" } }),
  coolingDown: await scenario({ cooldownUntil: 1_000_500 }),
  cooldownOver: await scenario({ cooldownUntil: 999_000 }),
  nearlyFull: await scenario({ nearlyFull: true }),
  otherFailure: await scenario({ fail: { 6: 404 } }),
};
results.statuses = (() => {
  const { window } = sandbox();
  const s = window.offlineAuto.networkStatus;
  return {
    wifi: s({ type: "wifi" }, false),
    cellular: s({ type: "cellular" }, true),
    unknownType: s({ type: "unknown" }, false),
    noApi: s(undefined, false),
    noApiAllowed: s(undefined, true),
    saveDataAllowed: s({ saveData: true }, true),
  };
})();
process.stdout.write(JSON.stringify(results));
