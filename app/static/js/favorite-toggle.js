// Wires up the star button on each "Читаю" card (PR 123) - toggles the title as the
// visitor's one favorite (POST /library/{slug_url}/favorite, see
// app/api/library.py's toggle_favorite()) without a page reload, same delegation
// pattern as recent-titles-forget.js.
//
// Since PR 252 the star sits beside the cover link rather than inside a card <a>, and
// its state is aria-pressed alone (the CSS styles [aria-pressed="true"]) - no separate
// --active class to keep in sync.
//
// Exactly one favorite per user (app/db/library.py's set_favorite() clears every other
// row server-side) - marking a title favorite here has to clear the star on whichever
// other card in this same grid was previously active, not just set this one.
//
// PR 275: the library page also shows the favorite as a blurred-cover card
// ([data-role="library-favorite"]) and its count on the "Избранное" tab - both are
// redrawn here from the star's own data-title-* attributes, so they follow the star
// without a reload.
(() => {
  const grid = document.querySelector('[data-role="library-titles"]');
  if (!grid) return;

  function setButtonState(button, isFavorite) {
    button.setAttribute("aria-pressed", isFavorite ? "true" : "false");
    const label = isFavorite ? "Убрать из избранного" : "Добавить в избранное";
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  const favoriteBlock = document.querySelector('[data-role="library-favorite"]');
  const favoriteTabLink = document.querySelector('.library-tabs__link[href="/library?tab=favorites"]');

  function favoriteCard(button) {
    const card = document.createElement("a");
    card.className = "wn-library-fav";
    card.href = `/titles/${button.dataset.slugUrl}`;
    card.dataset.role = "library-favorite-card";
    const cover = button.dataset.titleCover;
    if (cover) {
      const backdrop = document.createElement("img");
      backdrop.className = "wn-library-fav__backdrop";
      backdrop.src = cover;
      backdrop.alt = "";
      card.append(backdrop);
    }
    const scrim = document.createElement("span");
    scrim.className = "wn-library-fav__scrim";
    const coverBox = document.createElement("span");
    coverBox.className = "wn-library-fav__cover";
    if (cover) {
      const img = document.createElement("img");
      img.src = cover;
      img.alt = "";
      coverBox.append(img);
    }
    const copy = document.createElement("span");
    copy.className = "wn-library-fav__copy";
    const name = document.createElement("span");
    name.className = "wn-library-fav__name";
    name.textContent = button.dataset.titleName;
    const meta = document.createElement("span");
    meta.className = "wn-library-fav__meta";
    meta.textContent = button.dataset.titleMeta;
    copy.append(name, meta);
    card.append(scrim, coverBox, copy);
    return card;
  }

  function syncFavoriteBlock(button, isFavorite) {
    const count = isFavorite ? "1" : "0";
    const tabCount = favoriteTabLink?.querySelector(".library-tabs__count");
    if (tabCount) tabCount.textContent = count;
    if (!favoriteBlock) return;
    const countEl = favoriteBlock.querySelector('[data-role="library-favorite-count"]');
    if (countEl) countEl.textContent = count;
    const slot = favoriteBlock.querySelector('[data-role="library-favorite-slot"]');
    if (!slot) return;
    if (isFavorite) {
      slot.replaceChildren(favoriteCard(button));
    } else {
      const empty = document.createElement("p");
      empty.className = "wn-library__hint";
      empty.dataset.role = "library-favorite-empty";
      empty.textContent = "Отметьте тайтл звёздочкой — он появится здесь и в профиле.";
      slot.replaceChildren(empty);
    }
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
          '[data-role="favorite-toggle-trigger"][aria-pressed="true"]'
        );
        for (const other of previouslyActive) {
          if (other !== button) setButtonState(other, false);
        }
      }
      setButtonState(button, isFavorite);
      syncFavoriteBlock(button, isFavorite);
    } finally {
      button.disabled = false;
    }
  });
})();
