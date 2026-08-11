// Wires up the "×" button on each download-history row (PR 57) - a destructive action,
// so it asks for confirmation before sending anything, then removes the row on success
// rather than reloading the whole page.
(() => {
  const list = document.querySelector(".downloads-history");
  if (!list) return;

  list.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-role="delete-history-entry"]');
    if (!button) return;

    const row = button.closest("[data-entry-id]");
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
