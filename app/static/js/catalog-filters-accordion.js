// Collapsible sections inside the filters panel (PR 99, doработка PR 85/98) - the genre
// list alone runs to 50+ checkboxes, taking up most of the panel even for a visitor who
// only cares about narrowing by country. Each section's header is its own accordion
// toggle, independent of the others. Sections render fully expanded with no `hidden`
// anywhere (see catalog.html), so collapsing still works exactly as before for anyone
// without JS - it just can't be collapsed.
(() => {
  document.querySelectorAll('[data-role="catalog-filters-section"]').forEach((section) => {
    const toggle = section.querySelector('[data-role="catalog-filters-section-toggle"]');
    const options = section.querySelector('[data-role="catalog-filters-section-options"]');
    if (!toggle || !options) return;

    function isCollapsed() {
      return section.classList.contains("catalog-filters__section--collapsed");
    }

    function setCollapsed(collapsed) {
      section.classList.toggle("catalog-filters__section--collapsed", collapsed);
      options.hidden = collapsed;
      toggle.setAttribute("aria-expanded", String(!collapsed));
    }

    toggle.addEventListener("click", () => setCollapsed(!isCollapsed()));
  });
})();
