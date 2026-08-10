// Animated open/close for the catalog genre-filter panel (PR 45). A native <details>
// (used here through PR 38-44) snaps its content to display:none the instant it closes,
// with no way to run a CSS transition on the way out - so this is a plain button + panel
// with a class toggle instead, same pattern as chapter-export-panel.js (PR 37) and
// sidebar-toggle.js (PR 39).
//
// Without JS the panel (app/templates/catalog.html) is always visible, same as before
// this PR - a safe no-JS fallback, consistent with those other two scripts.
(() => {
  const toggle = document.querySelector('[data-role="catalog-genres-toggle"]');
  const panel = document.querySelector('[data-role="catalog-genres-panel"]');
  if (!toggle || !panel) return;

  panel.classList.add("catalog-genres__panel--js");

  toggle.addEventListener("click", () => {
    const open = panel.classList.toggle("catalog-genres__panel--open");
    toggle.setAttribute("aria-expanded", String(open));
  });
})();
