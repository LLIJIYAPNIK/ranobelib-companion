// Runs app/static/js/catalog-scroll.js in a node:vm sandbox with a fake grid, a fake
// IntersectionObserver and a scripted fetch, then prints what each scenario did (which
// URLs were fetched, what the live regions said, the grid's cursor) as JSON for
// tests/test_catalog_scroll_js.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

async function run(responses, steps, filters = {}) {
  const fetched = [];
  const appended = [];
  let observerCallback = null;
  const observed = new Set();
  const clickListeners = [];

  const grid = {
    dataset: { nextPage: "2", shown: "30", featured: "2", sort: "last_chapter_at", ...filters },
    attrs: {},
    skeletons: 0,
    insertAdjacentHTML(_where, html) {
      const count = (html.match(/catalog-grid__skeleton/g) || []).length;
      if (count) this.skeletons += count;
      else appended.push(html);
    },
    querySelectorAll() {
      const n = this.skeletons;
      this.skeletons = 0;
      return Array.from({ length: n }, () => ({ remove() {} }));
    },
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
  };
  const sentinel = {};
  const status = { textContent: "" };
  const errorBox = {
    innerHTML: "",
    addEventListener: (name, fn) => clickListeners.push(fn),
  };
  const end = { hidden: true };
  const elements = {
    '[data-role="catalog-end"]': end,
    '[data-role="catalog-grid"]': grid,
    '[data-role="catalog-sentinel"]': sentinel,
    '[data-role="catalog-loading"]': status,
    '[data-role="catalog-load-error"]': errorBox,
  };
  const loadingSeen = [];

  class FakeObserver {
    constructor(callback, options) {
      observerCallback = callback;
      this.options = options;
      FakeObserver.last = this;
    }
    observe(el) {
      observed.add(el);
    }
    unobserve(el) {
      observed.delete(el);
    }
  }

  let call = 0;
  const sandbox = {
    document: { querySelector: (sel) => elements[sel] ?? null },
    window: { addEventListener() {} },
    IntersectionObserver: FakeObserver,
    URLSearchParams,
    Number,
    fetch: (url) => {
      fetched.push(url);
      loadingSeen.push(status.textContent);
      const r = responses[call++];
      if (r === "network") return Promise.reject(new Error("offline"));
      return Promise.resolve({
        ok: r.ok,
        text: () => Promise.resolve(r.html ?? ""),
        headers: { get: (name) => (r.headers || {})[name] ?? null },
      });
    },
  };
  vm.runInNewContext(source, sandbox);

  const log = [];
  for (const step of steps) {
    if (step === "scroll") observerCallback([{ isIntersecting: true }]);
    if (step === "retry") {
      const target = { closest: (sel) => (sel === '[data-role="catalog-retry"]' ? {} : null) };
      clickListeners.forEach((fn) => fn({ target }));
    }
    await flush();
    await flush();
    log.push({
      error: errorBox.innerHTML.includes("Не удалось загрузить ещё"),
      retry: errorBox.innerHTML.includes("Повторить"),
      status: status.textContent,
      busy: grid.attrs["aria-busy"] ?? null,
    });
  }
  return {
    rootMargin: FakeObserver.last.options.rootMargin,
    fetched,
    loadingSeen,
    appended: appended.length,
    cursor: { nextPage: grid.dataset.nextPage, shown: grid.dataset.shown, featured: grid.dataset.featured },
    observing: observed.has(sentinel),
    endShown: end.hidden === false,
    log,
  };
}

const ok = (html, more, shown, featured) => ({
  ok: true,
  html,
  headers: {
    "X-Has-Next-Page": more ? "true" : "false",
    "X-Catalog-Shown": shown,
    "X-Catalog-Featured": featured,
  },
});

console.log(
  JSON.stringify({
    success: await run([ok("<a>cards</a>", true, "60", "5"), ok("<a>more</a>", false, "90", "7")], [
      "scroll",
      "scroll",
    ]),
    networkFailureThenRetry: await run(["network", ok("<a>cards</a>", true, "60", "5")], [
      "scroll",
      "scroll",
      "retry",
    ]),
    serverError: await run([{ ok: false }], ["scroll"]),
    // PR 303: the status and chapter-count filters ride along on every page.
    filters: await run([ok("<a>cards</a>", false, "30", "2")], ["scroll"], {
      sort: "views",
      statuses: "1,2",
      minChapters: "100",
    }),
  })
);
