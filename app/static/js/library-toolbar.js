// PR 275 (Webnovells Redesign, screen A3): the library toolbar - search by name, sort,
// progress filter and the list/grid switch - over the cards the server already rendered
// (a personal library is small, so this is plain client-side filtering, no extra
// request). The toolbar is `hidden` in the markup and only shown here, so without JS the
// page is just the full list.
//
// Also drops the cover skeleton shimmer (.wn-skeleton) once each cover image has loaded.
(() => {
  for (const img of document.querySelectorAll(".wn-skeleton img")) {
    const done = () => img.closest(".wn-skeleton")?.classList.add("is-loaded");
    if (img.complete) done();
    else {
      img.addEventListener("load", done, { once: true });
      img.addEventListener("error", done, { once: true });
    }
  }

  const toolbar = document.querySelector('[data-role="library-toolbar"]');
  const titles = document.querySelector('[data-role="library-titles"]');
  if (!toolbar || !titles) return;
  toolbar.hidden = false;

  const search = toolbar.querySelector('[data-role="library-search"]');
  const sort = toolbar.querySelector('[data-role="library-sort"]');
  const progress = toolbar.querySelector('[data-role="library-progress"]');
  const viewButtons = [...toolbar.querySelectorAll("[data-view]")];
  const noResults = titles.querySelector('[data-role="library-no-results"]');
  const VIEW_KEY = "libraryView";

  function matchesProgress(value, pct) {
    if (value === "not-started") return pct < 0;
    if (value === "lt50") return pct >= 0 && pct < 50;
    if (value === "gte50") return pct >= 50;
    return true;
  }

  const collator = new Intl.Collator("ru");
  const comparators = {
    recent: (a, b) => Number(a.dataset.order) - Number(b.dataset.order),
    added: (a, b) => (b.dataset.added || "").localeCompare(a.dataset.added || ""),
    name: (a, b) => collator.compare(a.dataset.name, b.dataset.name),
    progress: (a, b) => Number(b.dataset.progress) - Number(a.dataset.progress),
  };

  function apply() {
    const query = search.value.trim().toLowerCase();
    let visibleTotal = 0;
    for (const list of titles.querySelectorAll('[data-role="library-list"]')) {
      const items = [...list.querySelectorAll('[data-role="library-item"]')];
      items.sort(comparators[sort.value] || comparators.recent);
      let visible = 0;
      for (const item of items) {
        const show =
          (!query || item.dataset.name.includes(query)) &&
          matchesProgress(progress.value, Number(item.dataset.progress));
        item.hidden = !show;
        if (show) visible += 1;
        list.append(item);
      }
      const section = list.closest('[data-role="library-section"]');
      if (section) section.hidden = visible === 0;
      visibleTotal += visible;
    }
    if (noResults) noResults.hidden = visibleTotal > 0;
  }

  function setView(view) {
    titles.classList.toggle("wn-library__titles--grid", view === "grid");
    for (const button of viewButtons) {
      button.setAttribute("aria-checked", button.dataset.view === view ? "true" : "false");
    }
  }

  search.addEventListener("input", apply);
  sort.addEventListener("change", apply);
  progress.addEventListener("change", apply);
  for (const button of viewButtons) {
    button.addEventListener("click", () => {
      setView(button.dataset.view);
      try {
        localStorage.setItem(VIEW_KEY, button.dataset.view);
      } catch {
        // Storage can be unavailable (private mode) - the switch still works for this visit.
      }
    });
  }

  let savedView = null;
  try {
    savedView = localStorage.getItem(VIEW_KEY);
  } catch {
    savedView = null;
  }
  if (savedView === "grid") setView("grid");
})();
