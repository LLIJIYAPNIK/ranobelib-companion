// Wires up the "×" button on each "Недавние" card (PR 69) - removes the title from the
// visitor's own recent-titles cookie (POST /recent/{slug_url}/forget, see
// app/recent_titles.py's forget()) and drops the card from the page without a reload,
// same delegation pattern as download-history-delete.js.
//
// Since PR 250 the button sits next to the cover link (ui_title_card()'s caller block),
// not inside it, so a click no longer navigates - preventDefault()/stopPropagation() stay
// as a guard for anything else listening on the grid.
(() => {
  const grid = document.querySelector('[data-role="recent-titles"]');
  if (!grid) return;

  grid.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-role="forget-recent-title"]');
    if (!button) return;

    event.preventDefault();
    event.stopPropagation();

    // The home page's cards carry data-role="recent-item"; other pages keep the card.
    const card = button.closest('[data-role="recent-item"], .ui-title-card');
    const slugUrl = button.dataset.slugUrl;
    if (!card || !slugUrl) return;

    button.disabled = true;
    try {
      const response = await fetch(`/recent/${slugUrl}/forget`, { method: "POST" });
      if (response.ok) {
        card.remove();
        // No «Недавно открытые» at all once there's nothing left in it.
        if (!grid.querySelector('[data-role="recent-item"]')) grid.remove();
      } else {
        button.disabled = false;
      }
    } catch {
      button.disabled = false;
    }
  });
})();
