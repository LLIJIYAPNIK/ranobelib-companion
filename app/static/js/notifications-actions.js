// PR 170: mark-read/delete on any notification card - the bell panel's (client-rendered,
// notifications-panel.js, PR 168) and /notifications page's (server-rendered,
// _notification_card.html, PR 169) alike. One delegated listener on document rather than
// one per surface: the two render their otherwise-identical cards through entirely
// different code paths (client JS vs Jinja) and neither knows about the other's cards -
// delegation only needs the shared data-role/data-notification-id contract between them,
// not a shared render function.
//
// Deliberately does NOT mark anything read just because the bell panel was opened - only
// an explicit click on the checkmark does that. Auto-marking on open would make the
// button itself pointless (everything shown would already be read by the time a visitor
// could click it) and would clear the badge before they've actually looked at anything,
// which is worse for a popover that's easy to open by accident or close by clicking
// outside mid-glance.
//
// Also deliberately no bulk "Отметить все прочитанными" in this PR - per-notification
// actions cover the roadmap requirement, and a handful of unread items at a time (this is
// a comment-reaction feed, not a high-volume inbox) doesn't yet justify a second control
// surface. Revisit if a future notification kind makes volume a real problem.
(() => {
  const badge = document.querySelector('[data-role="notifications-badge"]');
  const panelList = document.querySelector('[data-role="notifications-list"]');
  const panelEmpty = document.querySelector('[data-role="notifications-empty"]');

  function applyUnreadCount(count) {
    if (!badge) return;
    badge.hidden = count === 0;
    if (count > 0) badge.textContent = count > 9 ? "9+" : String(count);
  }

  document.addEventListener("click", async (event) => {
    const markReadBtn = event.target.closest('[data-role="notification-mark-read"]');
    const deleteBtn = event.target.closest('[data-role="notification-delete"]');
    if (!markReadBtn && !deleteBtn) return;

    const item = event.target.closest("[data-notification-id]");
    if (!item) return;
    // The button sits outside the card's own <a> now (see _notification_card.html/
    // notifications-panel.js), so this isn't strictly needed to stop a navigation - kept
    // anyway so a future card layout change can't silently reintroduce that bug.
    event.preventDefault();

    const id = item.dataset.notificationId;
    const inPanel = Boolean(panelList && panelList.contains(item));
    let response;
    try {
      response = markReadBtn
        ? await fetch(`/notifications/${id}/read`, { method: "POST" })
        : await fetch(`/notifications/${id}`, { method: "DELETE" });
    } catch {
      return;
    }
    if (!response.ok) return;
    const data = await response.json();
    applyUnreadCount(data.unread_count);

    // The bell panel only ever lists unread notifications (PR 179, app/db/notifications.py's
    // list_recent_notifications()) - marking one read there has to remove the card, not
    // just drop the --unread modifier and its button, or the panel would show a card the
    // server would never have sent it in the first place on the next open. The full
    // /notifications page (_notification_card.html) keeps the old in-place behavior: it's
    // a history, a read notification still belongs there. Delete already removed the card
    // outright on both surfaces before this PR - only mark-read's panel behavior changes.
    if (markReadBtn && inPanel) {
      item.remove();
    } else if (markReadBtn) {
      item.classList.remove("notifications-panel__item--unread");
      markReadBtn.remove();
    } else {
      item.remove();
    }

    if (inPanel && panelEmpty) {
      panelEmpty.hidden = panelList.querySelector(".notifications-panel__item") !== null;
    }
  });
})();
