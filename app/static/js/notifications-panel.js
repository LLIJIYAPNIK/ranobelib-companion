// PR 168: sidebar bell + its flyout panel. The trigger is a real .sidebar__link (a
// <button> with data-role instead of an <a href>, since it opens a panel rather than
// navigating) so it gets the exact same collapsed/expanded 44px-icon-then-label treatment
// as Главная/Библиотека/Загрузки/Активность/Настройки above it for free - no wrapper
// element around it, unlike .profile-menu (that one needs position: relative as an
// anchor point regardless; this one doesn't; see position() below, which anchors off the
// sidebar's own edge instead of the trigger).
//
// Portal/click-outside/Escape mechanics copied from profile-menu.js (PR 97) - same
// .sidebar overflow-y: auto clipping problem, same fix (move the panel to <body>,
// position: fixed, restore it on close). Positioning itself differs: this panel always
// opens flush against the sidebar's own right edge, not relative to the trigger - it's
// the sidebar's flyout, not a dropdown hanging off one specific icon.
//
// The panel's own content (the recent-notifications list) is still just the static empty
// state at this point - rendering the real list is the rest of this PR, on top of this
// same open()/close().
(() => {
  const GAP = 8;
  const UNREAD_POLL_INTERVAL = 15000;

  const sidebar = document.querySelector('[data-role="sidebar"]');
  const trigger = document.querySelector('[data-role="notifications-trigger"]');
  const panel = document.querySelector('[data-role="notifications-panel"]');
  const badge = document.querySelector('[data-role="notifications-badge"]');
  if (!sidebar || !trigger || !panel) return;

  const homeParent = panel.parentElement;
  const homeNextSibling = panel.nextSibling;

  function isOpen() {
    return panel.classList.contains("notifications-panel--open");
  }

  function position() {
    const sidebarRect = sidebar.getBoundingClientRect();
    const triggerRect = trigger.getBoundingClientRect();
    panel.style.left = `${sidebarRect.right + GAP}px`;
    const top = Math.min(triggerRect.top, window.innerHeight - panel.offsetHeight - GAP);
    panel.style.top = `${Math.max(GAP, top)}px`;
  }

  function open() {
    document.body.appendChild(panel);
    panel.style.position = "fixed";
    position();
    panel.classList.add("notifications-panel--open");
    trigger.setAttribute("aria-expanded", "true");
    window.addEventListener("resize", closeOnLayoutChange);
    window.addEventListener("scroll", closeOnLayoutChange, true);
  }

  function close(refocusTrigger = false) {
    panel.classList.remove("notifications-panel--open");
    trigger.setAttribute("aria-expanded", "false");
    window.removeEventListener("resize", closeOnLayoutChange);
    window.removeEventListener("scroll", closeOnLayoutChange, true);
    homeParent.insertBefore(panel, homeNextSibling);
    panel.style.position = "";
    panel.style.top = "";
    panel.style.left = "";
    if (refocusTrigger) trigger.focus();
  }

  function closeOnLayoutChange() {
    close();
  }

  function applyUnreadCount(count) {
    if (!badge) return;
    badge.hidden = count === 0;
    if (count > 0) badge.textContent = count > 9 ? "9+" : String(count);
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
