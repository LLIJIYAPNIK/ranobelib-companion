// Wires up the "×" button on each download-history row (PR 57) - a destructive action,
// so it asks for confirmation before sending anything, then removes the row on success
// rather than reloading the whole page.
(() => {
  const list =
    document.querySelector('[data-role="download-history-groups"]') ||
    document.querySelector(".downloads-history");
  if (!list) return;

  list.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-role="delete-history-entry"]');
    if (!button) return;

    // Not `button.closest("[data-entry-id]")` (PR 70) - the button itself also carries
    // `data-entry-id`, so that selector matched the button and stopped there instead of
    // reaching the row, leaving the rest of the row on screen after a successful delete.
    const row = button.closest(".downloads-history__item");
    const entryId = button.dataset.entryId;
    if (!row || !entryId) return;

    if (!window.confirm("Удалить эту запись из истории загрузок?")) return;

    button.disabled = true;
    try {
      const response = await fetch(`/downloads/history/${entryId}`, { method: "DELETE" });
      if (response.ok) {
        row.remove();
        const group = button.closest(".wn-downloads-history__group");
        if (group && !group.querySelector(".downloads-history__item")) group.remove();
        const count = document.querySelector('[data-role="history-count"]');
        if (count) {
          const remaining = document.querySelectorAll(".downloads-history__item").length;
          count.textContent = String(remaining);
        }
      } else {
        button.disabled = false;
      }
    } catch {
      button.disabled = false;
    }
  });
})();
