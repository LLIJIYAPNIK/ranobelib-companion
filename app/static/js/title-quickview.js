// Quick view modal for title cards (PR 117) - the eye icon on a card opens a lightweight
// preview of the title without navigating away. Fetches GET /titles/{slug}/quickview
// (app/api/titles.py), a fragment that reuses show_title()'s own metadata-fetching call -
// no duplicated title-assembly logic on either side.
//
// PR 251 (Aurora Ink, CATALOG-QUICKVIEW): a <dialog class="ui-dialog"> with a scrim,
// skeleton while loading, Esc/«×»/scrim to close, focus back on the eye button. Opened
// with show(), not showModal(): a modal dialog sits in the top layer, above
// image-lightbox.js's overlay, and the cover inside this preview opens that lightbox
// (PR 142) - so this stays in normal z-index stacking, one level below it.
//
// On pages still on the old title_card() the eye sits inside the card's <a>, so a click
// would also navigate - preventDefault()/stopPropagation() stop that.
(() => {
  const scrim = document.createElement("div");
  scrim.className = "title-quickview-scrim";
  scrim.hidden = true;

  const dialog = document.createElement("dialog");
  dialog.className = "ui-dialog title-quickview-dialog";
  dialog.setAttribute("aria-label", "Быстрый просмотр");
  dialog.innerHTML = `
    <button type="button" class="ui-icon-btn title-quickview-dialog__close" aria-label="Закрыть">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>
    </button>
    <div class="title-quickview-dialog__body" data-role="title-quickview-body"></div>
  `;
  document.body.append(scrim, dialog);

  const body = dialog.querySelector('[data-role="title-quickview-body"]');
  const closeBtn = dialog.querySelector(".title-quickview-dialog__close");
  const SKELETON = `
    <div class="title-quickview" aria-busy="true">
      <span class="ui-skeleton ui-skeleton--cover title-quickview__cover"></span>
      <div class="title-quickview__info">
        <span class="ui-skeleton" style="height: 26px; width: 70%"></span>
        <span class="ui-skeleton ui-skeleton--line" style="width: 40%"></span>
        <span class="ui-skeleton ui-skeleton--line" style="width: 90%"></span>
        <span class="ui-skeleton ui-skeleton--line" style="width: 80%"></span>
      </div>
    </div>`;

  let returnFocus = null;
  let requestId = 0;

  function close() {
    if (!dialog.open) return;
    dialog.close();
    scrim.hidden = true;
    returnFocus?.focus();
    returnFocus = null;
  }

  async function open(slugUrl, trigger) {
    const id = ++requestId;
    returnFocus = trigger;
    body.innerHTML = SKELETON;
    scrim.hidden = false;
    if (!dialog.open) dialog.show();
    closeBtn.focus();
    try {
      const response = await fetch(`/titles/${slugUrl}/quickview`);
      if (!response.ok) throw new Error("bad response");
      const html = await response.text();
      if (id === requestId) body.innerHTML = html;
    } catch {
      if (id === requestId) {
        body.innerHTML = '<p class="ui-notice ui-notice--error">Не удалось загрузить превью</p>';
      }
    }
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest('[data-role="title-quickview-trigger"]');
    if (!trigger) return;
    event.preventDefault();
    event.stopPropagation();
    open(trigger.dataset.slugUrl, trigger);
  });

  scrim.addEventListener("click", close);
  closeBtn.addEventListener("click", close);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !dialog.open) return;
    // The lightbox opened from this preview's cover closes first, on its own Esc.
    if (document.querySelector(".image-lightbox--open")) return;
    close();
  });
})();
