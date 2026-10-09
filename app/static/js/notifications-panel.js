// PR 168: sidebar bell + its flyout panel. Since PR 249 (Aurora Ink) the bell is
// .sidebar__bell inside .sidebar__account - above the avatar at the bottom of the
// desktop rail, next to it in the mobile top strip - and the panel opens beside the rail
// on desktop (see position() below, which anchors off the sidebar's own edge).
//
// Portal/click-outside/Escape mechanics copied from profile-menu.js (PR 97) - same
// .sidebar overflow-y: auto clipping problem, same fix (move the panel to <body>,
// position: fixed, restore it on close). Positioning itself differs on desktop: this
// panel opens flush against the sidebar's own right edge, not relative to the trigger -
// it's the sidebar's flyout there, not a dropdown hanging off one specific icon.
//
// PR 214: that desktop positioning doesn't carry over to mobile, where .sidebar itself
// becomes the fixed, full-width bottom bar (PR 71; the Quiet Edge Bar since PR 249) - sidebarRect.right below would
// then equal the full viewport width, placing the panel entirely off-screen to the right
// instead of anywhere near the bell. The bell doesn't even live in .sidebar's own bottom
// row on mobile any more (PR 213 moved it up into .sidebar__account), so "flush against
// .sidebar's edge" was never the right frame of reference there to begin with. position()
// below branches on the same breakpoint the rest of the mobile adaptation uses, and on
// mobile anchors the panel to the viewport's own right edge and to just below the fixed
// top account strip instead - the same "float in the corner" language .download-ready/
// .cookie-notice already use for their own mobile positioning.
(() => {
  const GAP = 8;
  const UNREAD_POLL_INTERVAL = 15000;
  const mobileQuery = window.matchMedia("(max-width: 767px)");

  const sidebar = document.querySelector('[data-role="sidebar"]');
  const trigger = document.querySelector('[data-role="notifications-trigger"]');
  const panel = document.querySelector('[data-role="notifications-panel"]');
  const badge = document.querySelector('[data-role="notifications-badge"]');
  if (!sidebar || !trigger || !panel) return;

  const list = panel.querySelector('[data-role="notifications-list"]');
  const headerCount = panel.querySelector('[data-role="notifications-panel-count"]');

  const skeletonMarkup = list.innerHTML;

  const homeParent = panel.parentElement;
  const homeNextSibling = panel.nextSibling;

  function isOpen() {
    return panel.classList.contains("notifications-panel--open");
  }

  function position() {
    if (mobileQuery.matches) {
      panel.style.left = "";
      panel.style.bottom = "";
      panel.style.right = "12px";
      panel.style.top = `calc(var(--mobile-account-height) + env(safe-area-inset-top, 0px) + ${GAP}px)`;
      return;
    }
    // PR 249: the bell sits at the bottom of the rail, so the panel is anchored by its
    // bottom edge (level with the bell) and grows upward - its height changes once
    // loadRecent() fills the list, which a top-anchored panel would push off-screen.
    const sidebarRect = sidebar.getBoundingClientRect();
    const triggerRect = trigger.getBoundingClientRect();
    panel.style.right = "";
    panel.style.top = "auto";
    panel.style.left = `${sidebarRect.right + GAP}px`;
    panel.style.bottom = `${Math.max(GAP, window.innerHeight - triggerRect.bottom)}px`;
  }

  function open() {
    document.body.appendChild(panel);
    panel.style.position = "fixed";
    panel.classList.add("notifications-panel--open");
    position();
    trigger.setAttribute("aria-expanded", "true");
    window.addEventListener("resize", closeOnLayoutChange);
    window.addEventListener("scroll", closeOnLayoutChange, true);
    // PR 311: focus moves into the panel (it's portaled to the end of <body>, so Tab
    // from the bell would never reach it); Escape and closing by focus leaving it
    // hand focus back to the bell.
    panel.focus({ preventScroll: true });
    loadRecent();
  }

  function close(refocusTrigger = false) {
    panel.classList.remove("notifications-panel--open");
    trigger.setAttribute("aria-expanded", "false");
    window.removeEventListener("resize", closeOnLayoutChange);
    window.removeEventListener("scroll", closeOnLayoutChange, true);
    homeParent.insertBefore(panel, homeNextSibling);
    panel.style.position = "";
    panel.style.top = "";
    panel.style.bottom = "";
    panel.style.left = "";
    panel.style.right = "";
    if (refocusTrigger) trigger.focus();
  }

  // PR 311: scrolling the panel's own list isn't a layout change - only a scroll of
  // the page (or any other container) that would leave the panel floating misplaced.
  function closeOnLayoutChange(event) {
    if (event && event.type === "scroll" && panel.contains(event.target)) return;
    close();
  }

  // PR 311: the badge and the panel header's «N новых» - also re-applied whenever
  // notifications-actions.js reports a fresh count after a mark-read/delete.
  // PR 337: and the app icon's badge (app-badge.js), the same number.
  function applyUnreadCount(count) {
    window.appBadge?.update(count);
    if (badge) {
      badge.hidden = count === 0;
      if (count > 0) badge.textContent = count > 9 ? "9+" : String(count);
    }
    if (headerCount) {
      headerCount.hidden = count === 0;
      headerCount.textContent = `${count} ${plural(count, "новое", "новых", "новых")}`;
    }
  }

  function plural(n, one, few, many) {
    if (n % 10 === 1 && n % 100 !== 11) return one;
    if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return few;
    return many;
  }

  function showError() {
    list.innerHTML =
      '<div class="wn-notices-error" role="alert">' +
      '<span class="wn-notices-error__text">Не удалось загрузить уведомления</span>' +
      '<button type="button" class="wn-notices-error__retry" data-role="notifications-retry">Повторить</button>' +
      "</div>";
  }

  // PR 311: the cards come from GET /notifications/panel - the same server macro as the
  // /notifications page (_notification_card.html), not a second renderer here. The
  // skeleton base.html ships in the list shows until the first load; a reopen keeps the
  // previous cards on screen while they refresh.
  async function loadRecent() {
    list.setAttribute("aria-busy", "true");
    try {
      const response = await fetch("/notifications/panel");
      if (!response.ok) throw new Error(String(response.status));
      list.innerHTML = await response.text();
      applyUnreadCount(Number(response.headers.get("X-Unread-Count")) || 0);
    } catch {
      if (!list.querySelector("[data-notification-id]")) showError();
    } finally {
      list.removeAttribute("aria-busy");
    }
  }

  async function pollUnreadCount() {
    try {
      const response = await fetch("/notifications/unread-count");
      if (response.ok) applyUnreadCount((await response.json()).unread_count);
    } catch {
      // Next tick tries again - same tolerance as downloads-status.js's own poll loop.
    }
    setTimeout(pollUnreadCount, UNREAD_POLL_INTERVAL);
  }

  trigger.addEventListener("click", () => (isOpen() ? close() : open()));
  window.addEventListener("sidebar:statechange", closeOnLayoutChange);
  window.addEventListener("notifications:unreadchange", (event) =>
    applyUnreadCount(event.detail.unreadCount)
  );

  list.addEventListener("click", (event) => {
    if (!event.target.closest('[data-role="notifications-retry"]')) return;
    list.innerHTML = skeletonMarkup;
    loadRecent();
  });

  // Tabbing out of the panel closes it, same as a click outside does.
  panel.addEventListener("focusout", (event) => {
    const next = event.relatedTarget;
    if (isOpen() && next && !panel.contains(next) && !trigger.contains(next)) close();
  });

  document.addEventListener("click", (event) => {
    if (isOpen() && !trigger.contains(event.target) && !panel.contains(event.target)) {
      close();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close(true);
  });

  pollUnreadCount();
})();
