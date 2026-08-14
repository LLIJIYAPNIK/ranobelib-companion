// "Фильтры" toggle (PR 98, replacing PR 85's permanently-visible sidebar column) - the
// panel renders fully visible in catalog.html/app.css with no `hidden` attribute, so it
// still works exactly as before for anyone without JS (the checkboxes/radios inside it
// use `form="catalog-search-form"` regardless of where they're shown). This script is
// what turns it into something opened/closed by the toggle button instead of always
// occupying the sidebar column.
(() => {
  // Matches .catalog-filters--closing's transition duration in app.css - `hidden` can't
  // be part of a CSS transition, so this is how long JS waits before actually removing
  // the panel from layout once the fade/slide-out animation has had time to play.
  const CLOSE_TRANSITION_MS = 150;

  const toggle = document.querySelector('[data-role="catalog-filters-toggle"]');
  const panel = document.querySelector('[data-role="catalog-filters"]');
  if (!toggle || !panel) return;

  const closeButton = panel.querySelector('[data-role="catalog-filters-close"]');

  function isOpen() {
    return !panel.hidden;
  }

  function open() {
    panel.hidden = false;
    panel.classList.remove("catalog-filters--closing");
    toggle.setAttribute("aria-expanded", "true");
  }

  function close() {
    if (panel.hidden) return;
    panel.classList.add("catalog-filters--closing");
    toggle.setAttribute("aria-expanded", "false");
    window.setTimeout(() => {
      panel.hidden = true;
      panel.classList.remove("catalog-filters--closing");
    }, CLOSE_TRANSITION_MS);
  }

  panel.hidden = true;

  toggle.addEventListener("click", () => (isOpen() ? close() : open()));
  closeButton?.addEventListener("click", close);
})();
