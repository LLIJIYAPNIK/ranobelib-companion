// Mobile title actions (PR 253, M-TITLE-ACTIONS/-FORMAT/-REMOVE): «⋯» next to the
// primary CTA opens a bottom sheet with «Скачать тайтл» (-> the format sheet),
// «Скачать тома или главы» (-> the Оглавление tab in selection mode, see
// title-page-ui.js) and «Убрать из библиотеки» (-> a confirm sheet; a guest gets
// «Добавить в библиотеку» through the auth modal instead). The library bookmark under
// the title opens the same confirm sheet rather than removing straight away.
//
// The sheets are <dialog>s in _title_content.html opened with showModal(). Desktop never
// shows «⋯» - it keeps the inline download group and library toggle - so this only
// un-hides the trigger on mobile. Runs after title-content-load.js has injected the
// fragment (window.initTitleActionsSheet).
(() => {
  const mobileQuery = window.matchMedia("(max-width: 767px)");

  function initTitleActionsSheet() {
    const trigger = document.querySelector('[data-role="title-actions-trigger"]');
    const actions = document.querySelector('[data-role="title-actions-sheet"]');
    if (!trigger || !actions) return;
    const format = document.querySelector('[data-role="title-format-sheet"]');
    const remove = document.querySelector('[data-role="title-remove-sheet"]');
    const sheets = [actions, format, remove].filter(Boolean);

    function syncTrigger() {
      trigger.hidden = !mobileQuery.matches;
      if (!mobileQuery.matches) sheets.forEach((sheet) => sheet.open && sheet.close());
    }
    syncTrigger();
    mobileQuery.addEventListener("change", syncTrigger);

    // One sheet at a time; closing the last one returns focus to what opened the chain.
    let returnFocus = null;
    function show(sheet, opener) {
      if (!sheet) return;
      if (opener) returnFocus = opener;
      sheets.forEach((other) => other !== sheet && other.open && other.close());
      sheet.showModal();
    }
    function closeAll() {
      sheets.forEach((sheet) => sheet.open && sheet.close());
      returnFocus?.focus();
    }

    trigger.addEventListener("click", () => show(actions, trigger));

    sheets.forEach((sheet) => {
      sheet.querySelectorAll('[data-role="title-sheet-close"]').forEach((button) => {
        button.addEventListener("click", closeAll);
      });
      // A click on the dialog box itself (not its contents) is a click on the scrim.
      sheet.addEventListener("click", (event) => {
        if (event.target === sheet) closeAll();
      });
      sheet.addEventListener("cancel", (event) => {
        event.preventDefault();
        closeAll();
      });
    });

    actions.querySelector('[data-role="title-format-open"]')?.addEventListener("click", () => show(format));
    format?.querySelector('[data-role="title-format-back"]')?.addEventListener("click", () => show(actions));
    actions.querySelector('[data-role="title-remove-open"]')?.addEventListener("click", () => show(remove));

    actions.querySelector('[data-role="title-select-chapters"]')?.addEventListener("click", () => {
      sheets.forEach((sheet) => sheet.open && sheet.close());
      returnFocus = null;
      window.titlePageUi?.startChapterSelection();
    });

    // The chosen format's name on the submit button («Скачать EPUB»).
    const formatName = format?.querySelector('[data-role="title-format-name"]');
    format?.addEventListener("change", (event) => {
      if (formatName && event.target.name === "fmt") formatName.textContent = event.target.value.toUpperCase();
    });

    // Mobile bookmark while in the library: confirm first instead of submitting.
    const bookmark = document.querySelector('[data-role="title-remove-trigger"]');
    bookmark?.addEventListener("click", (event) => {
      if (!mobileQuery.matches || !remove) return;
      event.preventDefault();
      show(remove, bookmark);
    });
  }

  window.initTitleActionsSheet = initTitleActionsSheet;
})();
