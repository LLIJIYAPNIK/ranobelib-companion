// Infinite scroll for /notifications (PR 169) - same shape as catalog-scroll.js (PR 24):
// watches a sentinel element and, once it enters view, fetches the next page's card
// markup (app/api/notifications.py's notifications_page_fragment) and appends it, no
// client-side templating - the server renders the cards with the same
// _notification_card.html macro as the first page and the bell panel.
//
// PR 311 (Webnovells): the fragment comes as «Новые»/«Ранее» groups
// (_notification_groups.html) - a group already on the page gets the fetched cards
// appended to it, a new one is appended whole. A card that's already shown is skipped
// (mark-read/delete shift the server's offsets between pages). While a page loads, three
// skeleton cards and a role=status «Загружаем ещё…»; on failure a role=alert plate with
// «Повторить» that retries the same page; after the last page, «Это все уведомления».
(() => {
  const list = document.querySelector('[data-role="notifications-page-list"]');
  const sentinel = document.querySelector('[data-role="notifications-page-sentinel"]');
  if (!list || !sentinel) return;
  const skeletonTemplate = document.querySelector('[data-role="notifications-page-skeleton"]');
  const status = document.querySelector('[data-role="notifications-page-loading"]');
  const errorBox = document.querySelector('[data-role="notifications-page-error"]');
  const end = document.querySelector('[data-role="notifications-page-end"]');

  let nextPage = list.dataset.nextPage ? Number(list.dataset.nextPage) : null;
  let loading = false;
  let failed = false;

  function showLoading() {
    if (skeletonTemplate) {
      const skeleton = document.createElement("div");
      skeleton.className = "wn-notices__skeleton";
      skeleton.dataset.role = "notifications-page-skeleton-rows";
      skeleton.append(skeletonTemplate.content.cloneNode(true));
      list.append(skeleton);
    }
    list.setAttribute("aria-busy", "true");
    if (status) status.textContent = "Загружаем ещё…";
  }

  function hideLoading() {
    list
      .querySelectorAll('[data-role="notifications-page-skeleton-rows"]')
      .forEach((el) => el.remove());
    list.removeAttribute("aria-busy");
    if (status) status.textContent = "";
  }

  function showError() {
    failed = true;
    if (!errorBox) return;
    errorBox.innerHTML =
      '<div class="wn-notices-error" role="alert">' +
      '<span class="wn-notices-error__text">Не удалось загрузить ещё</span>' +
      '<button type="button" class="wn-notices-error__retry" data-role="notifications-page-retry">Повторить</button>' +
      "</div>";
  }

  function clearError() {
    failed = false;
    if (errorBox) errorBox.innerHTML = "";
  }

  function append(html) {
    const fragment = document.createElement("template");
    fragment.innerHTML = html;
    for (const group of fragment.content.querySelectorAll('[data-role="notifications-group"]')) {
      for (const card of group.querySelectorAll("[data-notification-id]")) {
        const id = card.dataset.notificationId;
        if (list.querySelector(`[data-notification-id="${CSS.escape(id)}"]`)) card.remove();
      }
      if (!group.querySelector("[data-notification-id]")) continue;
      const existing = list.querySelector(
        `[data-role="notifications-group"][data-group="${CSS.escape(group.dataset.group)}"]`
      );
      if (existing) {
        existing
          .querySelector('[data-role="notifications-group-list"]')
          .append(...group.querySelectorAll("[data-notification-id]"));
      } else {
        list.append(group);
      }
    }
  }

  // rootMargin extends the trigger zone below the viewport, same reasoning as
  // catalog-scroll.js's own observer - the next page starts loading while the visitor
  // still has some unread cards to scroll through.
  const observer = new IntersectionObserver(
    (entries) => {
      if (!failed && entries.some((entry) => entry.isIntersecting)) loadNextPage();
    },
    { rootMargin: "600px 0px" }
  );

  async function loadNextPage() {
    if (loading || !nextPage) return;
    loading = true;

    showLoading();
    let response;
    let html;
    try {
      response = await fetch(`/notifications/page?page=${nextPage}`);
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

    append(html);
    nextPage = response.headers.get("X-Has-Next-Page") === "true" ? nextPage + 1 : null;
    if (!nextPage) {
      observer.unobserve(sentinel);
      if (end && list.querySelector("[data-notification-id]")) end.hidden = false;
      return;
    }
    rearm();
  }

  if (errorBox) {
    errorBox.addEventListener("click", (event) => {
      if (!event.target.closest('[data-role="notifications-page-retry"]')) return;
      clearError();
      loadNextPage();
    });
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
