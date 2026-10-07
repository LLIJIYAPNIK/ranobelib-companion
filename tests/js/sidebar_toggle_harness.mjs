// Executes the synchronous restore script and deferred toggle script across three fake
// page loads sharing one localStorage instance. This models normal link navigation,
// where the DOM is replaced but the saved rail preference must survive.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const initSource = readFileSync(process.argv[2], "utf8");
const toggleSource = readFileSync(process.argv[3], "utf8");
const values = new Map();

function classList(initial = []) {
  const values = new Set(initial);
  return {
    add: (...names) => names.forEach((name) => values.add(name)),
    remove: (...names) => names.forEach((name) => values.delete(name)),
    contains: (name) => values.has(name),
    toggle: (name, force) => {
      const enabled = force === undefined ? !values.has(name) : force;
      if (enabled) values.add(name);
      else values.delete(name);
      return enabled;
    },
    snapshot: () => [...values].sort(),
  };
}

// PR 320: rail items whose label becomes a tooltip while collapsed (the third one has
// no label element, like an icon-only control, and never gets a title).
function railItem(labelText) {
  const attributes = {};
  const label = labelText === null ? null : { textContent: `  ${labelText}
` };
  return {
    get title() {
      return attributes.title ?? null;
    },
    set title(value) {
      attributes.title = value;
    },
    removeAttribute: (name) => delete attributes[name],
    querySelector: () => label,
  };
}

function loadPage() {
  const frames = [];
  const events = [];
  const listeners = {};
  const items = [railItem("Главная"), railItem("Войти"), railItem(null)];
  const sidebar = {
    classList: classList(),
    querySelectorAll: (selector) =>
      selector === ".sidebar__link, .sidebar__bell, .sidebar__guest" ? items : [],
  };
  const attributes = {};
  const toggle = {
    title: "",
    setAttribute: (name, value) => (attributes[name] = value),
    addEventListener: (name, callback) => (listeners[name] = callback),
  };
  const document = {
    currentScript: { parentElement: sidebar },
    querySelector: (selector) => {
      if (selector === '[data-role="sidebar"]') return sidebar;
      if (selector === '[data-role="sidebar-toggle"]') return toggle;
      return null;
    },
  };
  const localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const window = { dispatchEvent: (event) => events.push(event) };
  class CustomEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  }
  const context = {
    CustomEvent,
    document,
    localStorage,
    requestAnimationFrame: (callback) => frames.push(callback),
    window,
  };

  vm.runInNewContext(initSource, context);
  const beforeDeferred = sidebar.classList.snapshot();
  vm.runInNewContext(toggleSource, context);
  const afterDeferred = {
    classes: sidebar.classList.snapshot(),
    ariaExpanded: attributes["aria-expanded"],
    label: attributes["aria-label"],
    titles: items.map((item) => item.title),
  };
  while (frames.length) frames.shift()();

  return {
    beforeDeferred,
    afterDeferred,
    afterPaint: sidebar.classList.snapshot(),
    click: () => listeners.click(),
    snapshot: () => ({
      classes: sidebar.classList.snapshot(),
      ariaExpanded: attributes["aria-expanded"],
      label: attributes["aria-label"],
      titles: items.map((item) => item.title),
      stored: values.get("sidebarExpanded") ?? null,
      events: events.map((event) => ({ type: event.type, detail: event.detail })),
    }),
  };
}

const first = loadPage();
first.click();
const afterExpand = first.snapshot();
const second = loadPage();
second.click();
const afterCollapse = second.snapshot();
const third = loadPage();

console.log(
  JSON.stringify({
    firstBeforeDeferred: first.beforeDeferred,
    afterExpand,
    secondBeforeDeferred: second.beforeDeferred,
    secondAfterDeferred: second.afterDeferred,
    secondAfterPaint: second.afterPaint,
    afterCollapse,
    thirdBeforeDeferred: third.beforeDeferred,
    firstAfterDeferred: first.afterDeferred,
    thirdAfterDeferred: third.afterDeferred,
  }),
);
