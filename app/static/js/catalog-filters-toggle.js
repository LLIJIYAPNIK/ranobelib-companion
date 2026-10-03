// "Фильтры" toggle (PR 98, replacing PR 85's permanently-visible sidebar column) - the
// panel renders fully visible in catalog.html/app.css with no `hidden` attribute, so it
// still works exactly as before for anyone without JS (the checkboxes/radios inside it
// use `form="catalog-search-form"` regardless of where they're shown). This script is
// what turns it into something opened/closed by the toggle button instead of always
// occupying the sidebar column.
(() => {
  // Matches .catalog-filters--closing's transition (--dur-exit) in app.css - `hidden`
  // can't be part of a CSS transition, so this is how long JS waits before actually
  // removing the panel from layout once the fade/slide-out animation has had time to play.
  const CLOSE_TRANSITION_MS = 140;

  const toggle = document.querySelector('[data-role="catalog-filters-toggle"]');
  const panel = document.querySelector('[data-role="catalog-filters"]');
  if (!toggle || !panel) return;

  const closeButton = panel.querySelector('[data-role="catalog-filters-close"]');
  // PR 127: shifts (desktop) or hides (mobile, see app.css) the floating "back to top"
  // button so an open filters panel never sits on top of it - optional, since the button
  // itself only exists on catalog.html's own long/infinite-scroll grid.
  const backToTop = document.querySelector('[data-role="catalog-back-to-top"]');
  // PR 251: on mobile the panel is a bottom sheet (app.css) - the scrim behind it closes
  // it on tap, and the page under it stops scrolling while it's open
  // (body.catalog-filters-open, a no-op on desktop where the panel is a side column).
  const scrim = document.querySelector('[data-role="catalog-filters-scrim"]');

  // PR 296 (CatalogDesktop.dc.html): on desktop the panel is a popover right under the
  // toggle (app.css makes it position: fixed there) - placed from the toggle's own box,
  // and kept there while the sticky toolbar moves with the page.
  const desktopQuery = window.matchMedia("(min-width: 768px)");

  function place() {
    if (!desktopQuery.matches || panel.hidden) {
      panel.style.removeProperty("top");
      panel.style.removeProperty("right");
      panel.style.removeProperty("max-height");
      return;
    }
    const rect = toggle.getBoundingClientRect();
    const top = Math.round(rect.bottom + 8);
    panel.style.top = `${top}px`;
    panel.style.right = `${Math.max(16, Math.round(window.innerWidth - rect.right))}px`;
    panel.style.maxHeight = `${Math.max(240, window.innerHeight - top - 16)}px`;
  }

  function isOpen() {
    return !panel.hidden;
  }

  function open() {
    panel.hidden = false;
    place();
    panel.classList.remove("catalog-filters--closing");
    toggle.setAttribute("aria-expanded", "true");
    backToTop?.classList.add("back-to-top--filters-open");
    if (scrim) scrim.hidden = false;
    document.body.classList.add("catalog-filters-open");
  }

  function close() {
    if (panel.hidden) return;
    panel.classList.add("catalog-filters--closing");
    toggle.setAttribute("aria-expanded", "false");
    backToTop?.classList.remove("back-to-top--filters-open");
    document.body.classList.remove("catalog-filters-open");
    window.setTimeout(() => {
      panel.hidden = true;
      panel.classList.remove("catalog-filters--closing");
      if (scrim) scrim.hidden = true;
    }, CLOSE_TRANSITION_MS);
  }

  panel.hidden = true;
  // Without this class (no JS) the mobile panel stays an ordinary block under the grid
  // instead of a sheet nothing could ever close.
  panel.classList.add("catalog-filters--enhanced");

  toggle.addEventListener("click", () => (isOpen() ? close() : open()));
  closeButton?.addEventListener("click", close);
  scrim?.addEventListener("click", close);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close();
  });
  window.addEventListener("scroll", place, { passive: true });
  window.addEventListener("resize", place);
  desktopQuery.addEventListener("change", place);
  // The toolbar slides away on scroll-down (catalog-header-scroll.js) - follow it back.
  document
    .querySelector('[data-role="catalog-scroll-header"]')
    ?.addEventListener("transitionend", place);
})();
