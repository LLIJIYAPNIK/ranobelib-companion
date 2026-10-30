// Issue 399: a search typed into the catalog toolbar goes by popularity - the server's
// default for a query - unless the sort there is the visitor's own. The toolbar's
// <select name="sort"> always shows a sort (on /catalog, «По обновлению»), and a form
// sends every field, so without this the plain listing's default would ride along with
// every search as if picked. Before the form goes, a sort the visitor neither picked
// (no data-sort-kept: not in the URL, not a section's own) nor changed here is switched
// off, and a disabled field isn't sent. Without script the sort is sent as before.
(() => {
  const form = document.getElementById("catalog-search-form");
  const select = form?.querySelector('[data-role="catalog-sort"]');
  if (!select || select.hasAttribute("data-sort-kept")) return;

  let changed = false;
  select.addEventListener("change", () => {
    changed = true;
  });

  form.addEventListener("submit", () => {
    if (!changed) select.disabled = true;
  });

  // Back to this page from the results (bfcache): the select is the page's again.
  window.addEventListener("pageshow", () => {
    select.disabled = false;
  });
})();
