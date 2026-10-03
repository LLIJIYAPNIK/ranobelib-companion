// PR 294: keyboard for the «Библиотека / Каталог» switch (_library_switch.html,
// LibraryTabs.dc.html). The roving tabindex itself is rendered by the server - the
// active item is the only tab stop - so this only adds the tablist keys: ←/→ (wrapping)
// and Home/End move to the other item and follow its link straight away, as in the
// design, because each item is its own route (/library, /catalog), not a panel on this
// page. Space follows the focused item's link too; Enter already does, it's a link.
(() => {
  const list = document.querySelector('[data-role="library-switch"]');
  if (!list) return;
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  if (tabs.length < 2) return;

  function go(target) {
    tabs.forEach((tab) => {
      tab.tabIndex = tab === target ? 0 : -1;
    });
    target.focus();
    if (target.getAttribute("aria-selected") !== "true") window.location.assign(target.href);
  }

  list.addEventListener("keydown", (event) => {
    const current = tabs.indexOf(document.activeElement);
    if (current === -1) return;
    let next;
    if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else if (event.key === " ") next = current;
    else return;
    event.preventDefault();
    go(tabs[next]);
  });
})();
