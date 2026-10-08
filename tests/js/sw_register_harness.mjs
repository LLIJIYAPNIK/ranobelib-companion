// Runs app/static/js/sw-register.js in a node:vm sandbox with a fake
// navigator.serviceWorker and a minimal DOM, then prints each scenario's outcome as JSON
// for tests/test_service_worker.py: whether the update is offered, what «Обновить» and
// «Позже» do, and when the page reloads.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

function element(tag) {
  const listeners = {};
  const el = {
    tag,
    children: [],
    dataset: {},
    attributes: {},
    textContent: "",
    disabled: false,
    removed: false,
    setAttribute: (name, value) => (el.attributes[name] = value),
    addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
    append: (...nodes) => el.children.push(...nodes),
    remove: () => (el.removed = true),
    click: () => (listeners.click || []).forEach((fn) => fn({})),
  };
  return el;
}

async function run({ controller, waiting, postponed = false, click = null, controllerChange = false }) {
  const body = element("body");
  const storage = postponed ? { swUpdateLater: "1" } : {};
  const messages = [];
  const swListeners = {};
  let reloads = 0;
  const waitingWorker = waiting ? { postMessage: (data) => messages.push(data) } : null;
  const sandbox = {
    navigator: {
      serviceWorker: {
        controller: controller ? {} : null,
        register: (url, options) => {
          sandbox.registered = { url, scope: options.scope };
          return Promise.resolve({ waiting: waitingWorker });
        },
        addEventListener: (name, fn) => (swListeners[name] ||= []).push(fn),
      },
    },
    document: { body, createElement: element },
    sessionStorage: {
      getItem: (key) => storage[key] ?? null,
      setItem: (key, value) => (storage[key] = value),
    },
    window: { location: { reload: () => reloads++ } },
  };
  vm.runInNewContext(source, sandbox);
  await new Promise((resolve) => setTimeout(resolve, 0));

  const toast = body.children.find((node) => node.dataset.role === "sw-update");
  const button = (role) => toast?.children.find((node) => node.dataset.role === role);
  if (click) button(click).click();
  if (controllerChange) (swListeners.controllerchange || []).forEach((fn) => fn());
  return {
    registered: sandbox.registered,
    offered: Boolean(toast),
    text: toast?.children[0].textContent ?? null,
    buttons: toast ? toast.children.slice(1).map((b) => b.textContent) : [],
    messages,
    reloads,
    toastRemoved: toast?.removed ?? null,
    applyDisabled: button("sw-update-apply")?.disabled ?? null,
    storage,
  };
}

const results = {
  waiting: await run({ controller: true, waiting: true }),
  update: await run({ controller: true, waiting: true, click: "sw-update-apply", controllerChange: true }),
  later: await run({ controller: true, waiting: true, click: "sw-update-later" }),
  postponed: await run({ controller: true, waiting: true, postponed: true }),
  nothingWaiting: await run({ controller: true, waiting: false }),
  firstInstall: await run({ controller: false, waiting: true, controllerChange: true }),
  claimedWithoutTap: await run({ controller: true, waiting: true, controllerChange: true }),
};

process.stdout.write(JSON.stringify(results));
