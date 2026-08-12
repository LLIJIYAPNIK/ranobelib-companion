// Wires up the "×" button on each download-history row (PR 57) - a destructive action,
// so it asks for confirmation before sending anything, then removes the row on success
// rather than reloading the whole page.
(() => {
  const list = document.querySelector(".downloads-history");
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
      } else {
        button.disabled = false;
      }
    } catch {
      button.disabled = false;
    }
  });
})();
