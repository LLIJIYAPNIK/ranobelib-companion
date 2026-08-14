// Quick view modal for title cards (PR 117) - the eye icon in the top-left corner of
// every card (_title_card.html) opens a lightweight preview of the title without
// navigating away from the current page (Недавние/Читаю/Активность/каталог all use the
// same title_card() macro, so this covers every one of them). Fetches
// GET /titles/{slug}/quickview (app/api/titles.py), a fragment that reuses show_title()'s
// own metadata-fetching call - no duplicated title-assembly logic on either side.
//
// The whole card is an <a>, so a click on the eye button would otherwise also navigate to
// the title page - preventDefault()/stopPropagation() before opening the modal stops that,
// same pattern as recent-titles-forget.js's "×" button.
(() => {
  const overlay = document.createElement("div");
  overlay.className = "title-quickview-modal";
  overlay.innerHTML = `
    <div class="title-quickview-modal__panel" role="dialog" aria-modal="true" aria-label="Быстрый просмотр тайтла">
      <button type="button" class="title-quickview-modal__close" aria-label="Закрыть">&times;</button>
      <div class="title-quickview-modal__body" data-role="title-quickview-body"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  const body = overlay.querySelector('[data-role="title-quickview-body"]');
  const closeBtn = overlay.querySelector(".title-quickview-modal__close");

  function isOpen() {
    return overlay.classList.contains("title-quickview-modal--open");
  }

  function close() {
    overlay.classList.remove("title-quickview-modal--open");
  }

  async function open(slugUrl) {
    body.innerHTML = '<div class="spinner" aria-hidden="true"></div>';
    overlay.classList.add("title-quickview-modal--open");
    try {
      const response = await fetch(`/titles/${slugUrl}/quickview`);
      if (!response.ok) throw new Error("bad response");
      body.innerHTML = await response.text();
    } catch {
      body.innerHTML = '<p class="form-error">Не удалось загрузить превью</p>';
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest('[data-role="title-quickview-trigger"]');
    if (trigger) {
      event.preventDefault();
      event.stopPropagation();
      open(trigger.dataset.slugUrl);
      return;
    }
    // Clicking the backdrop itself (not the panel or any control inside it) closes it.
    if (isOpen() && event.target === overlay) close();
  });

  closeBtn.addEventListener("click", close);

  document.addEventListener("keydown", (event) => {
    if (isOpen() && event.key === "Escape") close();
  });
})();
