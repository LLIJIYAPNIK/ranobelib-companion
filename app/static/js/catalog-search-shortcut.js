// PR 296 (CatalogDesktop.dc.html): «/» anywhere on the catalog page focuses the search
// field - the hint the design shows inside it (<kbd>/</kbd>). Ignored while typing in a
// field, with a modifier key held, or when the key isn't a plain "/".
(() => {
  const input = document.querySelector('[data-role="catalog-search-input"]');
  if (!input) return;

  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
    ) {
      return;
    }
    event.preventDefault();
    input.focus();
    input.select();
  });
})();
