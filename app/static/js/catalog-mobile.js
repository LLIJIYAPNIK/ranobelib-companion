// PR 299 (CatalogMobile.dc.html, Catalog handoff.md -> Mobile 390 / 320): the catalog's
// phone controls.
//
// The ✕ inside the search field shows while it has text. It clears the field, and if the
// page was already showing a search it sends the form, so the feed drops that search too.
//
// Grid or list: two columns of covers (the default) or one column of row cards. The
// choice is remembered in localStorage. The grid's data-view attribute carries it, and
// app.css only reads it on phones.
(() => {
  const form = document.getElementById("catalog-search-form");
  const input = form?.querySelector('[data-role="catalog-search-input"]');
  const clear = form?.querySelector('[data-role="catalog-search-clear"]');
  const grid = document.querySelector('[data-role="catalog-grid"]');

  if (input && clear) {
    const sync = () => {
      clear.hidden = input.value === "";
    };
    input.addEventListener("input", sync);
    clear.addEventListener("click", () => {
      input.value = "";
      sync();
      if (grid?.dataset.query) form.requestSubmit();
      else input.focus();
    });
  }

  const toggle = document.querySelector('[data-role="catalog-view-toggle"]');
  if (!toggle || !grid) return;
  const VIEW_KEY = "catalogView";

  function setView(view) {
    grid.dataset.view = view;
    toggle.setAttribute("aria-label", view === "list" ? "Показать сеткой" : "Показать списком");
    toggle.dataset.view = view;
  }

  let saved = null;
  try {
    saved = localStorage.getItem(VIEW_KEY);
  } catch {
    // Storage blocked (private mode, cleared site data) - the grid it is.
  }
  setView(saved === "list" ? "list" : "grid");
  toggle.hidden = false;

  toggle.addEventListener("click", () => {
    const view = grid.dataset.view === "list" ? "grid" : "list";
    setView(view);
    try {
      localStorage.setItem(VIEW_KEY, view);
    } catch {
      // Not remembered - still switched for this page.
    }
  });
})();
