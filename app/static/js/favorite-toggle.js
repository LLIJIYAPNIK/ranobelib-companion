// Wires up the star button on each "Читаю" card (PR 123) - toggles the title as the
// visitor's one favorite (POST /library/{slug_url}/favorite, see
// app/api/library.py's toggle_favorite()) without a page reload, same delegation
// pattern as recent-titles-forget.js.
//
// The button sits inside the card's own <a> (title_card()'s caller block) so a click on
// it would otherwise also navigate to the title page - preventDefault()/stopPropagation()
// before the fetch stops that.
//
// Exactly one favorite per user (app/db/library.py's set_favorite() clears every other
// row server-side) - marking a title favorite here has to clear the star on whichever
// other card in this same grid was previously active, not just set this one.
(() => {
  const grid = document.querySelector('[data-role="library-titles"]');
  if (!grid) return;

  function setButtonState(button, isFavorite) {
    button.classList.toggle("title-card__favorite--active", isFavorite);
    button.setAttribute("aria-pressed", isFavorite ? "true" : "false");
    const label = isFavorite ? "Убрать из избранного" : "Добавить в избранное";
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  grid.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-role="favorite-toggle-trigger"]');
    if (!button) return;

    event.preventDefault();
    event.stopPropagation();

    const slugUrl = button.dataset.slugUrl;
    if (!slugUrl) return;

    button.disabled = true;
    try {
      const response = await fetch(`/library/${slugUrl}/favorite`, { method: "POST" });
      if (!response.ok) return;
      const { is_favorite: isFavorite } = await response.json();
      if (isFavorite) {
        const previouslyActive = grid.querySelectorAll(
          '[data-role="favorite-toggle-trigger"].title-card__favorite--active'
        );
        for (const other of previouslyActive) {
          if (other !== button) setButtonState(other, false);
        }
      }
      setButtonState(button, isFavorite);
    } finally {
      button.disabled = false;
    }
  });
})();
