// Runs app/static/js/reader-wake-lock.js in a node:vm sandbox - a fake screen Wake Lock
// API (or none, or one that refuses), page visibility, the reader setting and the page's
// lifecycle events - and prints each scenario's outcome as JSON for
// tests/test_reader_wake_lock_js.py (PR 338). Any throw or unhandled rejection fails the
// run.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");
const settle = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

function load({ api = "ok", on = false, visible = true, reader = true } = {}) {
  const log = [];
  const sentinels = [];
  let pending = null;

  class Sentinel {
    constructor() {
      this.released = false;
      this.listeners = [];
      sentinels.push(this);
    }
    addEventListener(name, fn) {
      if (name === "release") this.listeners.push(fn);
    }
    async release() {
      if (this.released) return;
      log.push("release");
      this.drop();
    }
    // The system lets go (tab hidden, screen locked).
    drop() {
      this.released = true;
      this.listeners.forEach((fn) => fn());
    }
  }

  const navigator = {};
  if (api !== "none") {
    navigator.wakeLock = {
      request(type) {
        log.push(`request:${type}`);
        if (api === "rejects") return Promise.reject(new Error("NotAllowedError"));
        if (api === "throws") throw new Error("SecurityError");
        if (api === "slow") {
          return new Promise((resolve) => (pending = () => resolve(new Sentinel())));
        }
        return Promise.resolve(new Sentinel());
      },
    };
  }

  const hints = [{ hidden: true }, { hidden: true }];
  const docListeners = {};
  const winListeners = {};
  const settings = { keepScreenOn: on };
  const document = {
    visibilityState: visible ? "visible" : "hidden",
    querySelector: (selector) => (selector === '[data-role="chapter"]' && reader ? {} : null),
    querySelectorAll: (selector) =>
      selector === '[data-role="keep-screen-on-unsupported"]' ? hints : [],
    addEventListener: (name, fn) => (docListeners[name] ||= []).push(fn),
  };
  const window = {
    readerSettings: { get: () => ({ ...settings }) },
    addEventListener: (name, fn) => (winListeners[name] ||= []).push(fn),
  };
  vm.runInNewContext(source, { window, document, navigator, Promise, Boolean });

  const fire = (listeners, name, event = {}) => (listeners[name] || []).forEach((fn) => fn(event));
  const api_ = {
    log,
    sentinels,
    hints,
    held: () => window.readerWakeLock.held(),
    supported: () => window.readerWakeLock.supported,
    finishRequest: () => pending?.(),
    setVisible(value) {
      document.visibilityState = value ? "visible" : "hidden";
      if (!value) sentinels.filter((s) => !s.released).forEach((s) => s.drop());
      fire(docListeners, "visibilitychange");
    },
    setSetting(value, key = "keepScreenOn") {
      settings.keepScreenOn = value;
      fire(docListeners, "reader-settings:change", { detail: { key } });
    },
    storage(value) {
      settings.keepScreenOn = value;
      fire(winListeners, "storage", { key: "readerSettings" });
    },
    pagehide: () => fire(winListeners, "pagehide", { persisted: true }),
    pageshow: (persisted) => fire(winListeners, "pageshow", { persisted }),
  };
  return api_;
}

const results = {};
const snap = (page) => ({ log: [...page.log], held: page.held() });

// Off (the default): never asked for.
{
  const page = load();
  await settle();
  results.off = snap(page);
}

// On: taken at load; hidden -> the system drops it; visible -> taken again, once.
{
  const page = load({ on: true });
  await settle();
  const atLoad = snap(page);
  page.setVisible(false);
  await settle();
  const hidden = snap(page);
  page.setVisible(true);
  page.setVisible(true); // a second event before the first request settled
  await settle();
  results.reacquire = { atLoad, hidden, visibleAgain: snap(page) };
}

// The system dropped it without a release event the page saw: still taken again.
{
  const page = load({ on: true });
  await settle();
  page.sentinels[0].released = true;
  page.setVisible(true);
  await settle();
  results.silentDrop = snap(page);
}

// Opened in a background tab: nothing until it's visible.
{
  const page = load({ on: true, visible: false });
  await settle();
  const background = snap(page);
  page.setVisible(true);
  await settle();
  results.background = { background, visible: snap(page) };
}

// Switched on and off from the Aa panel; reset (key null) keeps it, other keys don't care.
{
  const page = load();
  page.setSetting(true);
  await settle();
  const on = snap(page);
  page.setSetting(true, "fontSize");
  await settle();
  const otherKey = snap(page);
  page.setSetting(false);
  await settle();
  const off = snap(page);
  page.setSetting(true, null);
  await settle();
  results.toggle = { on, otherKey, off, reset: snap(page) };
}

// Changed in another tab.
{
  const page = load();
  page.storage(true);
  await settle();
  const on = snap(page);
  page.storage(false);
  await settle();
  results.otherTab = { on, off: snap(page) };
}

// Leaving the reader lets go; back from the back/forward cache takes it again.
{
  const page = load({ on: true });
  await settle();
  page.pagehide();
  await settle();
  const left = snap(page);
  page.pageshow(false);
  await settle();
  const notRestored = snap(page);
  page.pageshow(true);
  await settle();
  results.leave = { left, notRestored, back: snap(page) };
}

// Turned off while the request was still pending: the lock that arrives is let go.
{
  const page = load({ on: true, api: "slow" });
  await settle();
  page.setSetting(false);
  page.finishRequest();
  await settle();
  results.offWhilePending = snap(page);
}

// Left while the request was still pending: the lock that arrives is let go too.
{
  const page = load({ on: true, api: "slow" });
  await settle();
  page.pagehide();
  page.finishRequest();
  await settle();
  results.leftWhilePending = snap(page);
}

// Refused - asynchronously or right away: swallowed, and asked again next time.
for (const api of ["rejects", "throws"]) {
  const page = load({ on: true, api });
  await settle();
  page.setVisible(false);
  page.setVisible(true);
  await settle();
  results[api] = snap(page);
}

// No API: nothing happens, the settings page's hint shows; on the settings page (no
// reader) with the API, the hint stays hidden and nothing is requested.
{
  const page = load({ on: true, api: "none" });
  page.setSetting(true);
  page.setVisible(true);
  page.pagehide();
  await settle();
  results.noApi = { supported: page.supported(), hints: page.hints.map((h) => h.hidden), ...snap(page) };
  const settingsPage = load({ on: true, reader: false });
  await settle();
  results.settingsPage = {
    supported: settingsPage.supported(),
    hints: settingsPage.hints.map((h) => h.hidden),
    log: settingsPage.log,
  };
}

process.stdout.write(JSON.stringify(results));
