// Infinite scroll for the catalog tab: watches a sentinel element and, once it enters
// view, fetches the next page's card markup (app/api/library.py's catalog_page_fragment)
// and appends it - no client-side templating, the server renders the cards.
//
// PR 295 (Catalog handoff.md): the feed cursor travels with each request (see below);
// while a page loads, a row of skeleton cards and a role=status «Загружаем ещё…»; if it
// fails, a compact role=alert plate with «Повторить» that retries the same page and
// cursor - the cards already loaded stay, nothing reloads on its own until then.
(() => {
  const grid = document.querySelector('[data-role="catalog-grid"]');
  const sentinel = document.querySelector('[data-role="catalog-sentinel"]');
  if (!grid || !sentinel) return;
  const status = document.querySelector('[data-role="catalog-loading"]');
  const errorBox = document.querySelector('[data-role="catalog-load-error"]');
  // PR 295: «Вы посмотрели все тайтлы» - revealed once the last page is in.
  const end = document.querySelector('[data-role="catalog-end"]');

  let nextPage = grid.dataset.nextPage ? Number(grid.dataset.nextPage) : null;
  let loading = false;
  let failed = false;

  // PR 251 (CATALOG-DEFAULT), PR 295: a row of skeleton cards at the end of the grid
  // while a page loads - six, of which phones show two (app.css) - the same size as the
  // cards about to replace them.
  const SKELETON_HTML =
    '<div class="ui-title-card catalog-grid__skeleton" aria-hidden="true">' +
    '<span class="ui-skeleton ui-skeleton--cover"></span>' +
    '<span class="ui-skeleton ui-skeleton--line" style="width: 85%"></span></div>';

  function showLoading() {
    grid.insertAdjacentHTML("beforeend", SKELETON_HTML.repeat(6));
    grid.setAttribute("aria-busy", "true");
    if (status) status.textContent = "Загружаем ещё…";
  }

  function hideLoading() {
    grid.querySelectorAll(".catalog-grid__skeleton").forEach((el) => el.remove());
    grid.removeAttribute("aria-busy");
    if (status) status.textContent = "";
  }

  function showError() {
    failed = true;
    if (!errorBox) return;
    errorBox.innerHTML =
      '<div class="catalog-feed-error">' +
      '<span class="catalog-feed-error__text">Не удалось загрузить ещё</span>' +
      '<button type="button" class="catalog-feed-error__retry" data-role="catalog-retry">Повторить</button>' +
      "</div>";
  }

  function clearError() {
    failed = false;
    if (errorBox) errorBox.innerHTML = "";
  }

  // rootMargin extends the trigger zone below the viewport, so the next page starts
  // loading while the visitor still has some unread cards to scroll through instead of
  // only once they hit the very bottom.
  const observer = new IntersectionObserver(
    (entries) => {
      if (!failed && entries.some((entry) => entry.isIntersecting)) loadNextPage();
    },
    { rootMargin: "800px 0px" }
  );

  async function loadNextPage() {
    if (loading || !nextPage) return;
    loading = true;

    const params = new URLSearchParams({ page: String(nextPage) });
    if (grid.dataset.query) params.set("query", grid.dataset.query);
    if (grid.dataset.sort) params.set("sort", grid.dataset.sort);
    if (grid.dataset.genres) {
      for (const id of grid.dataset.genres.split(",")) params.append("genres", id);
    }
    // PR 100: countries is a repeated list param now (was a single value), same
    // comma-split-and-append shape as genres/tags above - data-country itself keeps its
    // pre-PR-100 attribute name, only its value format changed to a comma-joined list.
    if (grid.dataset.country) {
      for (const id of grid.dataset.country.split(",")) params.append("countries", id);
    }
    if (grid.dataset.tags) {
      for (const id of grid.dataset.tags.split(",")) params.append("tags", id);
    }
    // PR 295: the feed cursor - regular cards and featured inserts so far - so the
    // server keeps one featured insert per 12 cards across the whole feed.
    params.set("shown", grid.dataset.shown || "0");
    params.set("featured", grid.dataset.featured || "0");

    showLoading();
    let response;
    let html;
    try {
      response = await fetch(`/catalog/page?${params}`);
      html = response.ok ? await response.text() : null;
    } catch {
      html = null;
    }
    hideLoading();
    loading = false;

    if (html === null) {
      showError();
      return;
    }

    grid.insertAdjacentHTML("beforeend", html);
    grid.dataset.shown = response.headers.get("X-Catalog-Shown") || grid.dataset.shown;
    grid.dataset.featured = response.headers.get("X-Catalog-Featured") || grid.dataset.featured;
    nextPage = response.headers.get("X-Has-Next-Page") === "true" ? nextPage + 1 : null;
    if (!nextPage) {
      observer.unobserve(sentinel);
      if (end) end.hidden = false;
      return;
    }
    rearm();
  }

  if (errorBox) {
    errorBox.addEventListener("click", (event) => {
      if (!event.target.closest('[data-role="catalog-retry"]')) return;
      clearError();
      loadNextPage();
    });
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
