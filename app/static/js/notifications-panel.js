// PR 168: sidebar bell - the trigger is a real .sidebar__link (a <button> with data-role
// instead of an <a href>, since it eventually opens a panel rather than navigating) so it
// gets the exact same collapsed/expanded 44px-icon-then-label treatment as
// Главная/Библиотека/Загрузки/Активность/Настройки above it for free.
//
// Unread count polling only for now, same pattern as downloads-status.js's own badge poll
// (app/api/downloads_section.py's /downloads/status) - the panel itself, and the click
// handler that opens it, come later in this same PR.
(() => {
  const UNREAD_POLL_INTERVAL = 15000;

  const trigger = document.querySelector('[data-role="notifications-trigger"]');
  const badge = document.querySelector('[data-role="notifications-badge"]');
  if (!trigger) return;

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

  pollUnreadCount();
})();
