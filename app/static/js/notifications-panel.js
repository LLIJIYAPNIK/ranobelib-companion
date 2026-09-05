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

  // One explicit branch per kind (app/db/notifications.py's KIND_* constants) rather than
  // a generic "kind -> template string" table, same reasoning as paragraph-menu.js keeping
  // each attachment kind's rendering as its own explicit branch instead of a lookup table.
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
    } else if (notification.kind === "friend_request") {
      text.append(" отправил(а) вам заявку в друзья");
    } else if (notification.kind === "friend_accept") {
      text.append(" принял(а) вашу заявку в друзья");
    }
    return text;
  }

  // PR 199: friend_request/friend_accept aren't about a comment at all (comment_url is
  // always null for them) - they link to the actor's own profile instead.
  function linkUrlFor(notification) {
    if (notification.comment_url) return notification.comment_url;
    if (notification.kind === "friend_request" || notification.kind === "friend_accept") {
      return `/profile/${notification.actor_user_id}`;
    }
    return null;
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

  // PR 170: same outline-icon-string-via-innerHTML pattern as paragraph-menu.js's own
  // THUMB_ICON - one shared constant per icon rather than building each <svg> node by
  // node through the namespaced DOM API.
  const CHECK_ICON =
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M4 12.5 9.5 18 20 6"/></svg>';
  const TRASH_ICON =
    '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M5 6h14"/><path d="M9 6V4h6v2"/><path d="M7 6l1 14h8l1-14"/></svg>';

  // PR 170: mark-read/delete live outside the link now (own .notifications-panel__actions
  // row, own outer .notifications-panel__item wrapper) - see _notification_card.html's
  // own comment for why a <button> can no longer sit inside the <a> the way earlier PRs
  // had it. notifications-actions.js is what actually handles their clicks (one delegated
  // listener shared with the server-rendered cards on /notifications, PR 169) - this only
  // builds the buttons themselves, keyed by data-notification-id on the outer wrapper.
  function buildActions(notification) {
    const actions = document.createElement("div");
    actions.className = "notifications-panel__actions";
    if (!notification.is_read) {
      const markRead = document.createElement("button");
      markRead.type = "button";
      markRead.className = "notifications-panel__action";
      markRead.dataset.role = "notification-mark-read";
      markRead.title = "Отметить прочитанным";
      markRead.setAttribute("aria-label", "Отметить прочитанным");
      markRead.innerHTML = CHECK_ICON;
      actions.append(markRead);
    }
    const del = document.createElement("button");
    del.type = "button";
    del.className = "notifications-panel__action";
    del.dataset.role = "notification-delete";
    del.title = "Удалить";
    del.setAttribute("aria-label", "Удалить");
    del.innerHTML = TRASH_ICON;
    actions.append(del);
    return actions;
  }

  // An <a> when there's somewhere to send the visitor (linkUrlFor() above), a plain <div>
  // otherwise - a notification whose comment has since been deleted (PR 172) still shows,
  // it just isn't a link to a comment that no longer exists.
  function renderNotification(notification) {
    const item = document.createElement("div");
    item.className = "notifications-panel__item";
    if (!notification.is_read) item.classList.add("notifications-panel__item--unread");
    item.dataset.notificationId = notification.id;

    const linkUrl = linkUrlFor(notification);
    const link = document.createElement(linkUrl ? "a" : "div");
    link.className = "notifications-panel__item-link";
    if (linkUrl) link.href = linkUrl;

    const body = document.createElement("span");
    body.className = "notifications-panel__body";
    const time = document.createElement("span");
    time.className = "notifications-panel__time";
    time.textContent = formatTime(notification.created_at);
    body.append(buildNotificationText(notification), time);

    link.append(buildAvatar(notification), body);
    item.append(link, buildActions(notification));
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
