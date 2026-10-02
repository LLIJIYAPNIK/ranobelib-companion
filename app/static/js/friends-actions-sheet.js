// PR 285 (Webnovells Mobile -> Друзья): a friend card's «Ещё» on phones opens its actions
// - «Открыть профиль» / «Удалить из друзей» - in the shared bottom sheet (bottom-sheet.js),
// instead of the inline «Удалить» the desktop card shows. «Удалить из друзей» swaps the
// sheet to a confirmation (Отмена / Удалить, a plain POST form); closing the sheet puts it
// back to the menu for the next time.
(() => {
  function steps(content) {
    return {
      menu: content.querySelector('[data-role="friend-actions-menu"]'),
      confirm: content.querySelector('[data-role="friend-remove-confirm"]'),
    };
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-friend-sheet]");
    if (opener) {
      const content = document.getElementById(opener.dataset.friendSheet);
      if (!content || !window.bottomSheet) return;
      const { menu, confirm } = steps(content);
      window.bottomSheet.open({
        title: content.dataset.bottomSheetTitle,
        content,
        opener,
        onClose: () => {
          menu.hidden = false;
          confirm.hidden = true;
        },
      });
      return;
    }

    const ask = event.target.closest('[data-role="friend-remove-ask"]');
    if (!ask) return;
    const { menu, confirm } = steps(ask.closest('[data-role="friend-actions-sheet"]'));
    menu.hidden = true;
    confirm.hidden = false;
    // The focused button just disappeared - «Отмена» is the safe place to land.
    confirm.querySelector(".wn-sheet-confirm__cancel")?.focus({ preventScroll: true });
  });
})();
