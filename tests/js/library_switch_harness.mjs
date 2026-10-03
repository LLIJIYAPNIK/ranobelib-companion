// Runs app/static/js/library-switch.js in a node:vm sandbox with a fake two-tab
// tablist, presses keys and prints what each scenario did (where focus went, which
// tab stops are left, which URL it navigated to) as JSON for
// tests/test_library_switch_js.py.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(process.argv[2], "utf8");

function run(activeIndex, focusIndex, keys) {
  const listeners = {};
  const navigations = [];
  const document = { activeElement: null };
  const tabs = ["/library", "/catalog"].map((href, i) => {
    const tab = {
      href,
      tabIndex: i === activeIndex ? 0 : -1,
      getAttribute: (name) =>
        name === "aria-selected" ? String(i === activeIndex) : null,
      focus: () => {
        document.activeElement = tab;
      },
    };
    return tab;
  });
  const list = {
    querySelectorAll: () => tabs,
    addEventListener: (name, fn) => (listeners[name] ||= []).push(fn),
  };
  document.querySelector = (selector) =>
    selector === '[data-role="library-switch"]' ? list : null;
  const window = { location: { assign: (url) => navigations.push(url) } };
  vm.runInNewContext(source, { document, window });

  document.activeElement = tabs[focusIndex];
  const prevented = [];
  for (const key of keys) {
    const event = { key, preventDefault: () => prevented.push(key) };
    (listeners.keydown || []).forEach((fn) => fn(event));
  }
  return {
    focused: tabs.indexOf(document.activeElement),
    tabStops: tabs.map((t) => t.tabIndex),
    navigations,
    prevented,
  };
}

console.log(
  JSON.stringify({
    rightFromLibrary: run(0, 0, ["ArrowRight"]),
    leftFromLibraryWraps: run(0, 0, ["ArrowLeft"]),
    leftFromCatalog: run(1, 1, ["ArrowLeft"]),
    endFromLibrary: run(0, 0, ["End"]),
    homeOnLibrary: run(0, 0, ["Home"]),
    spaceOnActive: run(0, 0, [" "]),
    otherKeysIgnored: run(0, 0, ["Tab", "Enter", "a"]),
    focusOutsideIgnored: run(0, -1, ["ArrowRight"]),
  })
);
