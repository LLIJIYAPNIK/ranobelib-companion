// PR 213: on mobile, the bottom tab bar (.sidebar > .sidebar__link) had grown to seven
// items after PR 199 added "Друзья" - Главная/Библиотека/Загрузки/Активность/Друзья/
// Настройки/Уведомления, each with only a sliver of room on a typical phone width. Settings
// and the notification bell aren't really "navigate to a content section" links the way the
// other five are - they're account-related actions, closer in spirit to the profile menu
// that already lives in the separate top .sidebar__account strip (PR 71) than to the bottom
// bar's Главная/Библиотека/etc.
//
// This physically reparents the Settings link and the notifications bell (button + its
// flyout panel) into .sidebar__account-actions - a plain empty container inside
// .sidebar__account (base.html) - once the mobile breakpoint matches, and puts them back to
// their original spot in .sidebar's own list if the viewport grows back past it (matchMedia
// change fires on an actual breakpoint crossing, e.g. rotating a tablet or resizing a
// desktop window). Desktop's own vertical list layout never sees this script do anything.
//
// Without JS, both elements simply stay in their original .sidebar position - the mobile
// bar goes back to being seven items, exactly like before this PR, same "plain fallback"
// reasoning as settings-mobile-nav.js's own progressive enhancement.
(() => {
  const actions = document.querySelector('[data-role="sidebar-account-actions"]');
  if (!actions) return;

  const settingsLink = document.querySelector('[data-role="settings-link"]');
  const notificationsTrigger = document.querySelector('[data-role="notifications-trigger"]');
  const notificationsPanel = document.querySelector('[data-role="notifications-panel"]');

  // Recorded once, in their original document order, before anything ever moves - restoring
  // below replays this list back-to-front, so by the time an element's own recorded `next`
  // sibling is used as an insertBefore() reference, that sibling has already been reinserted
  // into `parent` (or was never moved to begin with).
  const homes = [settingsLink, notificationsTrigger, notificationsPanel]
    .filter(Boolean)
    .map((el) => ({ el, parent: el.parentElement, next: el.nextSibling }));

  const mobileQuery = window.matchMedia("(max-width: 640px)");

  function apply() {
    if (mobileQuery.matches) {
      for (const { el } of homes) actions.append(el);
    } else {
      for (const { el, parent, next } of [...homes].reverse()) {
        parent.insertBefore(el, next);
      }
    }
  }

  mobileQuery.addEventListener("change", apply);
  apply();
})();
