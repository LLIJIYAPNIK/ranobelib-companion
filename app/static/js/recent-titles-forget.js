// Wires up the "×" button on each "Недавние" card (PR 69) - removes the title from the
// visitor's own recent-titles cookie (POST /recent/{slug_url}/forget, see
// app/recent_titles.py's forget()) and drops the card from the page without a reload,
// same delegation pattern as download-history-delete.js.
//
// The button sits inside the card's own <a> (title_card()'s caller block) so a click on
// it would otherwise also navigate to the title page - preventDefault()/stopPropagation()
// before the fetch stops that.
(() => {
  const grid = document.querySelector('[data-role="recent-titles"]');
  if (!grid) return;

  grid.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-role="forget-recent-title"]');
    if (!button) return;

    event.preventDefault();
    event.stopPropagation();

    const card = button.closest(".title-card");
    const slugUrl = button.dataset.slugUrl;
    if (!card || !slugUrl) return;

    button.disabled = true;
    try {
      const response = await fetch(`/recent/${slugUrl}/forget`, { method: "POST" });
      if (response.ok) {
        card.remove();
      } else {
        button.disabled = false;
      }
    } catch {
      button.disabled = false;
    }
  });
})();
