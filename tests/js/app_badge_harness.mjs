// Runs app/static/js/app-badge.js in a node:vm sandbox - a navigator with or without the
// Badging API (or one that refuses), a page with or without the sidebar bell - and prints
// each scenario's outcome as JSON for tests/test_app_badge_js.py (PR 337). An unhandled
// rejection or a throw anywhere fails the whole run.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function load({ api = "ok", bell = false, readyState = "interactive" } = {}) {
  const calls = [];
  const navigator = {};
  if (api !== "none") {
    const answer = (name) => (...args) => {
      calls.push([name, ...args]);
      if (api === "rejects") return Promise.reject(new Error("NotAllowedError"));
      if (api === "throws") throw new Error("SecurityError");
      return Promise.resolve();
    };
    navigator.setAppBadge = answer("set");
    navigator.clearAppBadge = answer("clear");
  }
  const listeners = {};
  const document = {
    readyState,
    querySelector: (selector) =>
      bell && selector === '[data-role="notifications-trigger"]' ? { tag: "button" } : null,
    addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
  };
  const window = {};
  vm.runInNewContext(source, { window, document, navigator, Promise, Math, Number });
  const loaded = () => (listeners.DOMContentLoaded || []).forEach((fn) => fn());
  return { badge: window.appBadge, calls, loaded };
}

const results = {};

// A page with the bell: nothing until notifications-panel.js reports a count; then the
// count, once per change; 0 clears.
{
  const page = load({ bell: true });
  const atLoad = [...page.calls];
  for (const count of [3, 3, 12, "7", 0, 0, -2, 5.6]) page.badge.update(count);
  await settle();
  results.withBell = { atLoad, calls: page.calls, supported: page.badge.supported() };
}

// No bell (a guest, notifications off, «Не беспокоить»): a leftover badge is cleared.
{
  const page = load({ bell: false });
  results.noBell = page.calls;
}
{
  const page = load({ bell: false, readyState: "loading" });
  const before = [...page.calls];
  page.loaded();
  results.noBellStillLoading = { before, after: page.calls };
}

// clear() after a count (logout, device-account.js).
{
  const page = load({ bell: true });
  page.badge.update(4);
  page.badge.clear();
  page.badge.clear();
  results.clear = page.calls;
}

// No API at all (iOS outside the Home Screen app, most desktop tabs): nothing called,
// nothing thrown.
{
  const errors = [];
  for (const bell of [true, false]) {
    try {
      const page = load({ api: "none", bell });
      page.badge.update(3);
      page.badge.clear();
      results[bell ? "noApiBell" : "noApiNoBell"] = {
        supported: page.badge.supported(),
        calls: page.calls,
      };
    } catch (error) {
      errors.push(String(error));
    }
  }
  results.noApiErrors = errors;
}

// The API is there but refuses - asynchronously or right away: swallowed.
for (const api of ["rejects", "throws"]) {
  const errors = [];
  try {
    const page = load({ api, bell: false });
    page.badge.update(2);
    page.badge.clear();
    await settle();
    results[api] = { calls: page.calls, errors };
  } catch (error) {
    results[api] = { errors: [String(error)] };
  }
}

// Loaded twice on one page: one instance.
{
  const window = {};
  const navigator = { setAppBadge: async () => {}, clearAppBadge: async () => {} };
  const document = { readyState: "interactive", querySelector: () => ({}), addEventListener() {} };
  const context = { window, document, navigator, Promise, Math, Number };
  vm.runInNewContext(source, context);
  const first = window.appBadge;
  vm.runInNewContext(source, context);
  results.loadedTwice = window.appBadge === first;
}

process.stdout.write(JSON.stringify(results));
