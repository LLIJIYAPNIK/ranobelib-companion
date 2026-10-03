// PR 299 (CatalogMobile.dc.html, Catalog handoff.md -> Mobile 390 / 320): the catalog's
// phone controls.
//
// The ✕ inside the search field shows while it has text. It clears the field, and if the
// page was already showing a search it sends the form, so the feed drops that search too.
//
// Grid or list: two columns of covers (the default) or one column of row cards. The
// choice is remembered in localStorage. The grid's data-view attribute carries it, and
// app.css only reads it on phones.
//
// The filters sheet: the sort button and «Фильтры» both open #catalog-filters-sheet in
// the shared bottom sheet (bottom-sheet.js). The sheet's own form is a draft. Only
// «Показать результаты» sends it, with whatever is typed in the search field right now.
// «Сбросить» puts the draft back to «По обновлению» with no genres or countries.
// Closing the sheet any other way resets the form to what the page shows. The page
// gets .wn-catalog--sheet, and app.css then swaps the select and the popover toggle for
// these two buttons on phones.
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

  const page = document.querySelector(".wn-catalog");
  const sheet = document.querySelector('[data-role="catalog-filters-sheet"]');
  const sheetForm = sheet?.querySelector('[data-role="catalog-sheet-form"]');
  // bottom-sheet.js runs after this script (base.html loads it last), so its markup is
  // the check here and window.bottomSheet is only used once a button is tapped.
  if (page && sheetForm && document.querySelector('[data-role="bottom-sheet"]')) {
    const sheetQuery = sheetForm.querySelector('[data-role="catalog-sheet-query"]');
    const reset = sheet.querySelector('[data-role="catalog-sheet-reset"]');
    let applying = false;

    const openSheet = (event) => {
      window.bottomSheet?.open({
        title: sheet.dataset.bottomSheetTitle,
        content: sheet,
        opener: event.currentTarget,
        onClose: () => {
          if (!applying) sheetForm.reset();
        },
      });
    };
    for (const button of document.querySelectorAll('[data-role="catalog-sheet-open"]')) {
      button.hidden = false;
      button.addEventListener("click", openSheet);
    }
    page.classList.add("wn-catalog--sheet");

    reset?.addEventListener("click", () => {
      for (const field of sheetForm.querySelectorAll("input[type=radio], input[type=checkbox]")) {
        field.checked = field.type === "radio" && field.value === sheetForm.dataset.defaultSort;
      }
    });

    sheetForm.addEventListener("submit", () => {
      applying = true;
      if (sheetQuery && input) sheetQuery.value = input.value.trim();
    });

    // Back to this page from the results (bfcache): the sheet was left open on the way
    // out - close it, and the draft goes back to what this page shows.
    window.addEventListener("pageshow", (event) => {
      if (!event.persisted || !window.bottomSheet?.isOpen()) return;
      applying = false;
      window.bottomSheet.close();
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
