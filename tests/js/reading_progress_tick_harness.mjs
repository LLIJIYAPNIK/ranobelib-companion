// Runs app/static/js/reading-progress-tick.js in a node:vm sandbox with a fake DOM,
// a fake clock and a recording window.syncQueue (PR 332 - the script hands its requests
// to the device's queue, sync-queue.js, rather than fetching itself), then prints each
// scenario's requests as JSON for tests/test_reading_progress_tick_js.py to assert on.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

function run(savedParagraph, steps) {
  let now = 1_000_000;
  let nextId = 1;
  let timers = [];
  const listeners = {};
  const requests = [];
  const article = {
    dataset: { slugUrl: "6712--test-novel", volume: "1", number: "5", savedParagraph },
  };
  const document = {
    hidden: false,
    querySelector: (selector) => (selector === '[data-role="chapter"]' ? article : null),
    addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
  };
  const window = {
    addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
    syncQueue: {
      send: (url, fields, options = {}) => {
        requests.push({ url, at: now - 1_000_000, key: options.key ?? null, ...fields });
      },
    },
  };
  const fire = (name, event = {}) => (listeners[name] || []).forEach((fn) => fn(event));

  function advance(to) {
    for (;;) {
      const due = timers.filter((t) => t.at <= to).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      timers = timers.filter((t) => t !== due);
      now = Math.max(now, due.at);
      due.fn();
    }
    now = to;
  }

  const sandbox = {
    document,
    window,
    URLSearchParams,
    Number,
    Math,
    Date: { now: () => now },
    setTimeout: (fn, ms) => {
      const id = nextId++;
      timers.push({ id, fn, at: now + ms });
      return id;
    },
    clearTimeout: (id) => {
      timers = timers.filter((t) => t.id !== id);
    },
  };
  vm.runInNewContext(source, sandbox);

  const start = now;
  for (const step of steps) {
    advance(start + step.at);
    if (step.position) fire("reader:position", { detail: step.position });
    if (step.hide) {
      document.hidden = true;
      fire("visibilitychange");
    }
    if (step.pagehide) fire("pagehide");
  }
  advance(start + 60_000);
  return requests;
}

const total = 80;
const results = {
  // Ten taps 100 ms apart: the first goes out at once, the other nine fold into one
  // trailing request with the last position, 5 s after the first.
  quickTaps: run(
    "",
    Array.from({ length: 10 }, (_, i) => ({ at: i * 100, position: { revealed: i + 2, total } }))
  ),
  // Taps spaced wider than the throttle window each go out on their own.
  slowTaps: run("", [
    { at: 0, position: { revealed: 2, total } },
    { at: 6000, position: { revealed: 3, total } },
    { at: 12000, position: { revealed: 4, total } },
  ]),
  // Re-announcing the position the server already holds sends nothing.
  alreadyOnServer: run("50", [{ at: 0, position: { revealed: 50, total } }]),
  // A position still waiting for its trailing slot is flushed when the tab is hidden.
  flushOnHide: run("", [
    { at: 0, position: { revealed: 2, total } },
    { at: 1000, position: { revealed: 3, total } },
    { at: 1500, hide: true },
  ]),
  flushOnPagehide: run("", [
    { at: 0, position: { revealed: 2, total } },
    { at: 1000, position: { revealed: 3, total } },
    { at: 1500, pagehide: true },
  ]),
  // Nonsense positions are dropped instead of being sent for the server to 422.
  invalid: run("", [
    { at: 0, position: { revealed: 0, total } },
    { at: 10, position: { revealed: 81, total } },
    { at: 20, position: { revealed: "5", total } },
  ]),
};
process.stdout.write(JSON.stringify(results));
