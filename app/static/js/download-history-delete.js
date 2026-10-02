// The trash button on each download-history row (PR 57). Since PR 281 (Webnovells Mobile
// -> Загрузки) a delete is undoable instead of confirmed up front: the row leaves the page
// at once, a toast offers «Вернуть» for a few seconds, and only then is the DELETE sent.
// «Вернуть» puts the row back where it was without anything reaching the server. A delete
// still waiting is sent right away when another one starts, and with keepalive when the
// page is left, so leaving the page never silently undoes it.
(() => {
  const list =
    document.querySelector('[data-role="download-history-groups"]') ||
    document.querySelector(".downloads-history");
  if (!list) return;

  const UNDO_MS = 5000;
  let pending = null; // { entryId, row, parent, next, group, timer }
  let toast = null;

  function updateCount() {
    const count = document.querySelector('[data-role="history-count"]');
    if (count) count.textContent = String(document.querySelectorAll(".downloads-history__item").length);
  }

  function showToast(message, action) {
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "wn-toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      document.body.append(toast);
    }
    const text = document.createElement("span");
    text.className = "wn-toast__text";
    text.textContent = message;
    toast.replaceChildren(text);
    if (action) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "wn-toast__action";
      button.textContent = action.label;
      button.addEventListener("click", action.run);
      toast.append(button);
    }
    toast.hidden = false;
    clearTimeout(toast.hideTimer);
    toast.hideTimer = setTimeout(hideToast, action ? UNDO_MS : 2600);
  }

  function hideToast() {
    if (toast) toast.hidden = true;
  }

  function putBack(p) {
    p.parent.insertBefore(p.row, p.next && p.next.parentNode === p.parent ? p.next : null);
    if (p.group) p.group.hidden = false;
    p.row.querySelector('[data-role="delete-history-entry"]').disabled = false;
    updateCount();
  }

  async function send(p, { keepalive = false } = {}) {
    try {
      const response = await fetch(`/downloads/history/${p.entryId}`, { method: "DELETE", keepalive });
      if (response.ok) return;
    } catch {
      // Falls through to putting the row back.
    }
    if (keepalive) return;
    putBack(p);
    showToast("Не удалось удалить запись");
  }

  function commit() {
    if (!pending) return;
    const p = pending;
    pending = null;
    clearTimeout(p.timer);
    send(p);
  }

  function undo() {
    if (!pending) return;
    const p = pending;
    pending = null;
    clearTimeout(p.timer);
    putBack(p);
    hideToast();
    p.row.querySelector('[data-role="delete-history-entry"]').focus();
  }

  list.addEventListener("click", (event) => {
    const button = event.target.closest('[data-role="delete-history-entry"]');
    if (!button) return;

    // Not `button.closest("[data-entry-id]")` (PR 70) - the button itself also carries
    // `data-entry-id`, so that selector matched the button and stopped there instead of
    // reaching the row.
    const row = button.closest(".downloads-history__item");
    const entryId = button.dataset.entryId;
    if (!row || !entryId) return;

    commit();
    button.disabled = true;
    const group = row.closest(".wn-downloads-history__group");
    pending = { entryId, row, parent: row.parentNode, next: row.nextSibling, group, timer: null };
    row.remove();
    if (group && !group.querySelector(".downloads-history__item")) group.hidden = true;
    updateCount();
    pending.timer = setTimeout(commit, UNDO_MS);
    showToast("Удалено из истории", { label: "Вернуть", run: undo });
    // From the keyboard (no pointer, so detail is 0) the focused row is gone - hand focus
    // to «Вернуть» rather than dropping it on <body>.
    if (event.detail === 0) toast.querySelector(".wn-toast__action").focus();
  });

  window.addEventListener("pagehide", () => {
    if (!pending) return;
    const p = pending;
    pending = null;
    clearTimeout(p.timer);
    send(p, { keepalive: true });
  });
})();
