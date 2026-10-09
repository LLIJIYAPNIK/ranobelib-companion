// Runs app/static/js/device-account.js in a node:vm sandbox - fake Web Storage, a
// recording sync queue and downloads store, the logout form and its sheet - then prints
// each scenario's outcome as JSON for tests/test_device_account_js.py to assert on.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function settle() {
  for (let i = 0; i < 10; i += 1) await tick();
}

class FakeStorage {
  constructor(entries = {}) {
    this.map = new Map(Object.entries(entries));
  }
  get length() {
    return this.map.size;
  }
  key(i) {
    return [...this.map.keys()][i] ?? null;
  }
  getItem(key) {
    return this.map.has(key) ? this.map.get(key) : null;
  }
  setItem(key, value) {
    this.map.set(key, String(value));
  }
  removeItem(key) {
    this.map.delete(key);
  }
  snapshot() {
    return Object.fromEntries(this.map);
  }
}

const DEVICE = {
  "tapToReadProgress:/titles/a/chapters/1/1": '{"revealed":12,"total":40}',
  "tapToReadProgress:/titles/b/chapters/2/7": '{"revealed":3,"total":20}',
  "readerLastChapter:a": "1--1",
  readerSettings: '{"fontSize":19}',
  sidebarExpanded: "1",
  cookieNoticeDismissed: "1",
};

function load({ userId = "9", recorded, titles = [], flushHangs = false, withForm = true } = {}) {
  const log = [];
  const localStorage = new FakeStorage({ ...DEVICE, ...(recorded ? { wnDeviceAccount: recorded } : {}) });
  const sessionStorage = new FakeStorage({ downloadReadyDismissed: '["job-1"]', swUpdateLater: "1" });
  const timers = [];

  const form = {
    listeners: {},
    addEventListener(name, fn) {
      (this.listeners[name] ||= []).push(fn);
    },
    querySelector: () => ({ tag: "button" }),
    submit: () => log.push("submit"),
  };
  const button = (keep) => ({
    dataset: { logoutKeep: keep },
    disabled: false,
    listeners: [],
    addEventListener(name, fn) {
      this.listeners.push(fn);
    },
    click() {
      this.listeners.forEach((fn) => fn());
    },
  });
  const keep = button("1");
  const remove = button("0");
  const count = { textContent: "" };
  const sheet = {
    dataset: { bottomSheetTitle: "Выйти из аккаунта" },
    querySelectorAll: (selector) => (selector === "[data-logout-keep]" || selector === "button" ? [keep, remove] : []),
    querySelector: (selector) => (selector === '[data-role="logout-offline-count"]' ? count : null),
  };
  const opened = [];
  const window = {
    syncQueue: {
      flush: () => {
        log.push("flush");
        return flushHangs ? new Promise(() => {}) : Promise.resolve(true);
      },
      clear: async () => void log.push("clearQueue"),
    },
    offlineStore: {
      supported: () => true,
      listTitles: async () => titles,
      clearAll: async () => void log.push("clearDownloads"),
    },
    // PR 337: the app icon's badge (app-badge.js).
    appBadge: { clear: () => void log.push("clearBadge") },
    bottomSheet: { open: (options) => opened.push(options.title) },
    dispatchEvent: (event) => opened.push(`event:${event.type}`),
  };
  const document = {
    currentScript: { dataset: { userId } },
    getElementById: (id) => (id === "logout-offline-choice" ? sheet : null),
    querySelectorAll: (selector) => (selector === 'form[action="/logout"]' && withForm ? [form] : []),
  };
  vm.runInNewContext(source, {
    window,
    document,
    localStorage,
    sessionStorage,
    Promise,
    Event: class {
      constructor(type) {
        this.type = type;
      }
    },
    setTimeout: (fn, ms) => {
      timers.push(ms);
      setTimeout(fn, 0); // the wait itself is instant here; its length is recorded
    },
  });

  const submitForm = async () => {
    let prevented = false;
    for (const fn of form.listeners.submit || []) {
      await fn({ preventDefault: () => (prevented = true), submitter: null });
    }
    await settle();
    return prevented;
  };
  const state = () => ({
    log: [...log],
    local: localStorage.snapshot(),
    session: sessionStorage.snapshot(),
    opened: [...opened],
    count: count.textContent,
    timers: [...timers],
  });
  return { window, keep, remove, submitForm, state };
}

const results = {};

{
  const page = load({ recorded: undefined });
  await page.window.deviceAccount.ready;
  results.firstSignIn = page.state();
}
{
  const page = load({ recorded: "9" });
  await page.window.deviceAccount.ready;
  results.sameAccount = page.state();
}
{
  const page = load({ recorded: "7" });
  await page.window.deviceAccount.ready;
  results.switched = page.state();
}
{
  const page = load({ userId: "", recorded: "7", withForm: false });
  await page.window.deviceAccount.ready;
  results.signedOutPage = page.state();
}
{
  const page = load({ recorded: "9" });
  const prevented = await page.submitForm();
  results.logoutNothingDownloaded = { prevented, ...page.state() };
}
{
  const page = load({ recorded: "9", titles: [{ slug: "a" }, { slug: "b" }] });
  await page.submitForm();
  const asked = page.state();
  page.keep.click();
  await settle();
  results.logoutKeep = { asked, after: page.state() };
}
{
  const page = load({ recorded: "9", titles: [{ slug: "a" }] });
  await page.submitForm();
  page.remove.click();
  await settle();
  results.logoutDelete = page.state();
}
{
  const page = load({ recorded: "9", flushHangs: true });
  await page.submitForm();
  results.logoutFlushHangs = page.state();
}

process.stdout.write(JSON.stringify(results));
