// PR 251 (M-CATALOG): the mobile toolbar has no «Искать» - search submits on Enter,
// filters on «Показать», and picking a sort order submits right away. Only on mobile,
// and only once this script runs: it tags the toolbar, and the CSS hides «Искать» just
// for a tagged toolbar, so without JS the button is still there.
// "Случайно" is left to catalog-random-redirect.js.
(() => {
  const form = document.getElementById("catalog-search-form");
  const select = form?.querySelector('select[name="sort"]');
  const toolbar = document.querySelector('[data-role="catalog-scroll-header"]');
  if (!select || !toolbar) return;

  const mobileQuery = window.matchMedia("(max-width: 767px)");
  toolbar.classList.add("catalog-toolbar--autosubmit");

  select.addEventListener("change", () => {
    if (!mobileQuery.matches || select.value === "random") return;
    form.requestSubmit();
  });
})();
