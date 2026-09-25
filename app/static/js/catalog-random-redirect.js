// PR 230: picking "Случайно" in the catalog's sort <select> goes straight to one random
// title (GET /library/catalog/random) instead of reloading the list reshuffled - the
// moment it's chosen, not only after "Искать", since there's no list left to look at.
// The same interception runs on submit too, in case the select still reads "random" when
// the form is sent (e.g. the browser restored it on a back navigation).
//
// Works on the native <select> both with and without custom-dropdown.js (PR 54): that
// script keeps the real <select> live and fires "change" on it when an option is picked.
(() => {
  const form = document.getElementById("catalog-search-form");
  const select = form?.querySelector('select[name="sort"]');
  if (!select) return;
  const grid = document.querySelector('[data-role="catalog-grid"]');

  function randomUrl() {
    const params = new URLSearchParams();
    const data = new FormData(form);
    const query = (data.get("query") || "").trim();
    if (query) params.set("query", query);
    data.getAll("genres").forEach((id) => params.append("genres", id));
    data.getAll("countries").forEach((id) => params.append("countries", id));
    // Tags aren't form fields (they only arrive via a title page's tag badge link), so
    // the current page's own tag filter is carried over from the grid, same source
    // catalog-scroll.js uses for its page fetches.
    (grid?.dataset.tags || "").split(",").filter(Boolean).forEach((id) => params.append("tags", id));
    const tagName = new URLSearchParams(window.location.search).get("tag_name");
    if (tagName) params.set("tag_name", tagName);
    return `/library/catalog/random?${params}`;
  }

  select.addEventListener("change", () => {
    if (select.value === "random") window.location.href = randomUrl();
  });

  form.addEventListener("submit", (event) => {
    if (select.value !== "random") return;
    event.preventDefault();
    window.location.href = randomUrl();
  });
})();
