// "Фильтры" toggle (PR 98, replacing PR 85's permanently-visible sidebar column) - the
// panel renders fully visible in catalog.html/app.css with no `hidden` attribute, so it
// still works exactly as before for anyone without JS (the checkboxes/radios inside it
// use `form="catalog-search-form"` regardless of where they're shown). This script is
// what turns it into something opened/closed by the toggle button instead of always
// occupying the sidebar column.
(() => {
  const toggle = document.querySelector('[data-role="catalog-filters-toggle"]');
  const panel = document.querySelector('[data-role="catalog-filters"]');
  if (!toggle || !panel) return;

  function isOpen() {
    return !panel.hidden;
  }

  function open() {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
  }

  function close() {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  panel.hidden = true;

  toggle.addEventListener("click", () => (isOpen() ? close() : open()));
})();
