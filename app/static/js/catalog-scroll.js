// Infinite scroll for the catalog tab: watches a sentinel element and, once it enters
// view, fetches the next page's card markup (app/api/library.py's catalog_page_fragment)
// and appends it - no client-side templating, the server renders the cards.
(() => {
  const grid = document.querySelector('[data-role="catalog-grid"]');
  const sentinel = document.querySelector('[data-role="catalog-sentinel"]');
  if (!grid || !sentinel) return;

  let nextPage = grid.dataset.nextPage ? Number(grid.dataset.nextPage) : null;
  let loading = false;

  // rootMargin extends the trigger zone below the viewport, so the next page starts
  // loading while the visitor still has some unread cards to scroll through instead of
  // only once they hit the very bottom.
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadNextPage();
    },
    { rootMargin: "600px 0px" }
  );

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
    if (!nextPage) {
      observer.unobserve(sentinel);
      return;
    }
    rearm();
  }

  // IntersectionObserver only reports a *change* in intersection - if the sentinel is
  // still inside the trigger zone right after a page loads (e.g. on a tall/wide screen
  // where a page of cards never fills the viewport), the ratio never crosses the
  // threshold again and no further pages load on their own, even though there's plainly
  // room for more - only a manual resize/zoom, which forces a fresh layout pass, used to
  // unstick it. Re-observing the sentinel makes IntersectionObserver report its current
  // state fresh, the same way it does the first time observe() is called, so this covers
  // both "just appended more content" and "the viewport itself was resized" without
  // duplicating the rootMargin math by hand.
  function rearm() {
    observer.unobserve(sentinel);
    observer.observe(sentinel);
  }

  window.addEventListener("resize", rearm);
  observer.observe(sentinel);
})();
