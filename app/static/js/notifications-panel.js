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
(() => {
  const GAP = 8;
  const UNREAD_POLL_INTERVAL = 15000;

  const sidebar = document.querySelector('[data-role="sidebar"]');
  const trigger = document.querySelector('[data-role="notifications-trigger"]');
  const panel = document.querySelector('[data-role="notifications-panel"]');
  const badge = document.querySelector('[data-role="notifications-badge"]');
  if (!sidebar || !trigger || !panel) return;

  const list = panel.querySelector('[data-role="notifications-list"]');
  const empty = panel.querySelector('[data-role="notifications-empty"]');

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

  function formatTime(iso) {
    return new Date(iso).toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // Only kind today (app/db/notifications.py's KIND_COMMENT_REACTION) - a future kind
  // adds its own branch here rather than a generic "kind -> template string" table, same
  // reasoning as paragraph-menu.js keeping each attachment kind's rendering as its own
  // explicit branch instead of a lookup table.
  function buildNotificationText(notification) {
    const text = document.createElement("span");
    text.className = "notifications-panel__text";
    const actor = document.createElement("strong");
    actor.textContent = notification.actor_name;
    text.append(actor);
    if (notification.kind === "comment_reaction") {
      text.append(" отреагировал(а) на ваш комментарий");
      if (notification.comment_excerpt) {
        text.append(` «${notification.comment_excerpt}»`);
      }
    }
    return text;
  }

  // Same picture-or-initials pairing as paragraph-menu.js's buildCommentAvatar() -
  // actor_avatar_url/actor_avatar_initials arrive already computed by the same
  // server-side helpers (app/auth/avatar.py).
  function buildAvatar(notification) {
    const avatar = document.createElement("span");
    avatar.className = "notifications-panel__avatar";
    if (notification.actor_avatar_url) {
      const img = document.createElement("img");
      img.className = "avatar-img";
      img.src = notification.actor_avatar_url;
      img.alt = "";
      avatar.append(img);
    } else {
      avatar.textContent = notification.actor_avatar_initials;
    }
    return avatar;
  }

  // An <a> when there's somewhere to send the visitor (comment_url), a plain <div>
  // otherwise - a notification whose comment has since been deleted (PR 172) still shows,
  // it just isn't a link to a comment that no longer exists.
  function renderNotification(notification) {
    const item = document.createElement(notification.comment_url ? "a" : "div");
    item.className = "notifications-panel__item";
    if (!notification.is_read) item.classList.add("notifications-panel__item--unread");
    if (notification.comment_url) item.href = notification.comment_url;

    const body = document.createElement("span");
    body.className = "notifications-panel__body";
    const time = document.createElement("span");
    time.className = "notifications-panel__time";
    time.textContent = formatTime(notification.created_at);
    body.append(buildNotificationText(notification), time);

    item.append(buildAvatar(notification), body);
    return item;
  }

  function renderNotifications(notifications) {
    list.querySelectorAll(".notifications-panel__item").forEach((item) => item.remove());
    empty.hidden = notifications.length > 0;
    for (const notification of notifications) {
      list.append(renderNotification(notification));
    }
  }

  async function loadRecent() {
    try {
      const response = await fetch("/notifications/recent");
      if (!response.ok) return;
      const data = await response.json();
      applyUnreadCount(data.unread_count);
      renderNotifications(data.notifications);
    } catch {
      // Same "fails silently, whatever was already rendered stays" reasoning as
      // paragraph-menu.js's own fetches - the rest of the page still works either way.
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
