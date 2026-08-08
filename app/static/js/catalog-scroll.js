// Infinite scroll for the catalog tab: watches a sentinel element and, once it enters
// view, fetches the next page's card markup (app/api/library.py's catalog_page_fragment)
// and appends it - no client-side templating, the server renders the cards.
(() => {
  const grid = document.querySelector('[data-role="catalog-grid"]');
  const sentinel = document.querySelector('[data-role="catalog-sentinel"]');
  if (!grid || !sentinel) return;

  let nextPage = grid.dataset.nextPage ? Number(grid.dataset.nextPage) : null;
  let loading = false;

  const observer = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadNextPage();
  });

  async function loadNextPage() {
    if (loading || !nextPage) return;
    loading = true;

    const params = new URLSearchParams({ page: String(nextPage) });
    if (grid.dataset.query) params.set("query", grid.dataset.query);
    if (grid.dataset.sort) params.set("sort", grid.dataset.sort);

    let response;
    try {
      response = await fetch(`/library/catalog/page?${params}`);
    } catch {
      loading = false;
      return;
    }

    if (!response.ok) {
      observer.unobserve(sentinel);
      loading = false;
      return;
    }

    grid.insertAdjacentHTML("beforeend", await response.text());
    nextPage = response.headers.get("X-Has-Next-Page") === "true" ? nextPage + 1 : null;
    loading = false;
    if (!nextPage) observer.unobserve(sentinel);
  }

  observer.observe(sentinel);
})();
