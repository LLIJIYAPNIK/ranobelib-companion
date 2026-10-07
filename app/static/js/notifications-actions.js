// PR 170: mark-read/delete on any notification card - the bell panel's
// (notifications-panel.js, PR 168) and /notifications page's (PR 169) alike. One
// delegated listener on document rather than one per surface: neither surface knows
// about the other's cards, delegation only needs the shared data-role/
// data-notification-id contract between them.
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
//
// PR 311: both surfaces now render the same server macro, so the card contract is one
// class (.wn-notice) plus these data-roles. The fresh unread count goes out as a
// "notifications:unreadchange" event (notifications-panel.js owns the badge and the
// panel header). Removing a card moves focus to its neighbour - or, with none left, to
// the surface itself - instead of dropping it on <body>; an emptied «Новые»/«Ранее»
// group goes with its last card, and an emptied list shows its empty state.
(() => {
  const panelList = document.querySelector('[data-role="notifications-list"]');
  const pageList = document.querySelector('[data-role="notifications-page-list"]');

  function focusAfterRemoval(item, container) {
    const cards = Array.from(container.querySelectorAll("[data-notification-id]"));
    const index = cards.indexOf(item);
    const neighbour = cards[index + 1] || cards[index - 1];
    if (neighbour) {
      const target =
        neighbour.querySelector("a.wn-notice__link") || neighbour.querySelector("button");
      target?.focus();
    } else if (container === pageList) {
      document.querySelector('[data-role="notifications-page-title"]')?.focus();
    } else {
      document.querySelector('[data-role="notifications-panel"]')?.focus();
    }
  }

  function removeCard(item, container, hadFocus) {
    if (hadFocus) focusAfterRemoval(item, container);
    const group = item.closest('[data-role="notifications-group"]');
    item.remove();
    if (group && !group.querySelector("[data-notification-id]")) group.remove();
    if (container.querySelector("[data-notification-id]")) return;
    const empty =
      container === pageList
        ? document.querySelector('[data-role="notifications-page-empty"]')
        : container.querySelector('[data-role="notifications-empty"]');
    if (empty) empty.hidden = false;
    if (container === pageList) {
      const end = document.querySelector('[data-role="notifications-page-end"]');
      if (end) end.hidden = true;
    }
  }

  document.addEventListener("click", async (event) => {
    const markReadBtn = event.target.closest('[data-role="notification-mark-read"]');
    const deleteBtn = event.target.closest('[data-role="notification-delete"]');
    if (!markReadBtn && !deleteBtn) return;

    const item = event.target.closest("[data-notification-id]");
    if (!item) return;
    // The button sits outside the card's own <a> (see _notification_card.html), so this
    // isn't strictly needed to stop a navigation - kept anyway so a future card layout
    // change can't silently reintroduce that bug.
    event.preventDefault();

    // aria-busy rather than disabling the button: a disabled button drops its focus to
    // <body>, and focus has to survive the request to move on to the neighbour card.
    if (item.getAttribute("aria-busy") === "true") return;
    const id = item.dataset.notificationId;
    const container = panelList && panelList.contains(item) ? panelList : pageList;
    item.setAttribute("aria-busy", "true");
    let response;
    try {
      response = markReadBtn
        ? await fetch(`/notifications/${id}/read`, { method: "POST" })
        : await fetch(`/notifications/${id}`, { method: "DELETE" });
    } catch {
      response = null;
    }
    item.removeAttribute("aria-busy");
    if (!response || !response.ok) return;
    const data = await response.json();
    window.dispatchEvent(
      new CustomEvent("notifications:unreadchange", { detail: { unreadCount: data.unread_count } })
    );
    const hadFocus = item.contains(document.activeElement);

    // The bell panel only ever lists unread notifications (PR 179, app/db/notifications.py's
    // list_recent_notifications()) - marking one read there has to remove the card, not
    // just drop the --unread modifier and its button, or the panel would show a card the
    // server would never have sent it in the first place on the next open. The full
    // /notifications page keeps it in place: it's a history, a read notification still
    // belongs there (under «Ранее» from the next load on).
    if (markReadBtn && container !== panelList) {
      item.classList.remove("wn-notice--unread");
      item.querySelectorAll(".wn-notice__dot, .wn-notice__sr").forEach((el) => el.remove());
      markReadBtn.remove();
      if (hadFocus) item.querySelector('[data-role="notification-delete"]')?.focus();
    } else if (container) {
      removeCard(item, container, hadFocus);
    } else {
      item.remove();
    }
  });
})();
