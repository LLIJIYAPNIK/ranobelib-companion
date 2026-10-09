// Runs app/static/js/reader-copy-check.js in a node:vm sandbox - a downloaded copy's
// chapter element, the «Глава обновлена» banner, a fake fetch for the version endpoint,
// and a recording offline store / OfflineQueue - then prints each scenario's outcome as
// JSON for tests/test_reader_copy_check_js.py (PR 339).
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");
const settle = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function load({
  version = "aaaa",
  server = { status: 200, version: "aaaa" },
  onLine = true,
  copy = true,
  branchId = "",
  downloadFails = false,
} = {}) {
  const log = [];
  const dataset = { slugUrl: "6712--test-novel", volume: "1", number: "3", branchId };
  if (copy) dataset.offlineCopy = "1";
  if (version) dataset.contentVersion = version;
  const listeners = [];
  const text = { textContent: "Глава обновлена на сайте." };
  const button = {
    disabled: false,
    addEventListener: (name, fn) => listeners.push(fn),
    click: () => Promise.all(listeners.map((fn) => fn())),
  };
  const banner = {
    hidden: true,
    querySelector: (selector) =>
      ({
        '[data-role="reader-copy-updated-text"]': text,
        '[data-role="reader-copy-refresh"]': button,
      })[selector],
  };
  const document = {
    querySelector: (selector) => {
      if (selector === '[data-role="chapter"][data-offline-copy="1"]') return copy ? { dataset } : null;
      if (selector === '[data-role="reader-copy-updated"]') return banner;
      return null;
    },
  };
  const fetch = async (url) => {
    log.push(`fetch ${url}`);
    if (server === "offline") throw new TypeError("Failed to fetch");
    return {
      ok: server.status === 200,
      status: server.status,
      json: async () => ({ version: server.version }),
    };
  };
  const title = { slug: "6712--test-novel", name: "Повелитель тайн", cover: "/c", toc: [["1", "3"]] };
  const window = {
    location: { reload: () => log.push("reload") },
    offlineStore: {
      supported: () => true,
      listTitles: async () => [title],
      chaptersOf: async () => [{ volume: "1", number: "3", images: ["/img/old", "/img/kept"] }],
      dropUnusedImages: async (urls) => void log.push(`drop ${urls.join(",")}`),
    },
    OfflineQueue: {
      downloadChapter: async (record, item, signal) => {
        log.push(`download ${record.slug} ${JSON.stringify(item)} ${Boolean(signal)}`);
        if (downloadFails) throw new Error("429");
      },
    },
  };
  vm.runInNewContext(source, {
    window,
    document,
    fetch,
    navigator: { onLine },
    AbortController,
    Number,
    encodeURIComponent,
  });
  return {
    log,
    banner,
    text,
    button,
    state: () => ({ shown: !banner.hidden, text: text.textContent, disabled: button.disabled, log: [...log] }),
  };
}

const results = {};

const scenario = async (name, options) => {
  const page = load(options);
  await settle();
  results[name] = page.state();
  return page;
};

await scenario("same", {});
await scenario("updated", { server: { status: 200, version: "bbbb" } });
await scenario("branch", { branchId: "7", server: { status: 200, version: "bbbb" } });
await scenario("serverError", { server: { status: 429 } });
await scenario("networkFails", { server: "offline" });
await scenario("deviceOffline", { onLine: false, server: { status: 200, version: "bbbb" } });
await scenario("oldCopy", { version: "", server: { status: 200, version: "bbbb" } });
await scenario("onlineReader", { copy: false, server: { status: 200, version: "bbbb" } });

{
  const page = await scenario("refresh", { server: { status: 200, version: "bbbb" } });
  await page.button.click();
  await settle();
  results.refresh = page.state();
}
{
  const page = await scenario("refreshFails", {
    server: { status: 200, version: "bbbb" },
    downloadFails: true,
  });
  await page.button.click();
  await settle();
  results.refreshFails = page.state();
}

process.stdout.write(JSON.stringify(results));
