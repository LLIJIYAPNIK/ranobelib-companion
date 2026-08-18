// Infinite scroll for /notifications (PR 169) - same shape as catalog-scroll.js (PR 24):
// watches a sentinel element and, once it enters view, fetches the next page's card
// markup (app/api/notifications.py's notifications_page_fragment) and appends it, no
// client-side templating - the server renders the cards, same _notification_card.html
// macro notifications.html's own first page already used.
(() => {
  const list = document.querySelector('[data-role="notifications-page-list"]');
  const sentinel = document.querySelector('[data-role="notifications-page-sentinel"]');
  if (!list || !sentinel) return;

  let nextPage = list.dataset.nextPage ? Number(list.dataset.nextPage) : null;
  let loading = false;

  // rootMargin extends the trigger zone below the viewport, same reasoning as
  // catalog-scroll.js's own observer - the next page starts loading while the visitor
  // still has some unread cards to scroll through.
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadNextPage();
    },
    { rootMargin: "600px 0px" }
  );

  async function loadNextPage() {
    if (loading || !nextPage) return;
    loading = true;

    let response;
    try {
      response = await fetch(`/notifications/page?page=${nextPage}`);
    } catch {
      loading = false;
      return;
    }

    if (!response.ok) {
      observer.unobserve(sentinel);
      loading = false;
      return;
    }

    list.insertAdjacentHTML("beforeend", await response.text());
    nextPage = response.headers.get("X-Has-Next-Page") === "true" ? nextPage + 1 : null;
    loading = false;
    if (!nextPage) {
      observer.unobserve(sentinel);
      return;
    }
    rearm();
  }

  // Same re-observe-to-force-a-fresh-check reasoning as catalog-scroll.js's own rearm() -
  // covers both "just appended more content" and "the viewport itself was resized"
  // without duplicating the rootMargin math by hand.
  function rearm() {
    observer.unobserve(sentinel);
    observer.observe(sentinel);
  }

  window.addEventListener("resize", rearm);
  observer.observe(sentinel);
})();
