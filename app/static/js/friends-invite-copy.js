// PR 278: «Копировать» on the /friends invite card - puts the visitor's own nickname on
// the clipboard and says so on the button for a moment. Falls back to selecting the
// nickname text when the Clipboard API isn't available (insecure context, old browser),
// so it can still be copied by hand.
(() => {
  const button = document.querySelector('[data-role="friends-invite-copy"]');
  if (!button) return;
  const label = button.textContent;

  function selectNickname() {
    const nickname = document.querySelector('[data-role="friends-invite-nickname"]');
    if (!nickname) return;
    const range = document.createRange();
    range.selectNodeContents(nickname);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = "Скопировано";
      setTimeout(() => {
        button.textContent = label;
      }, 1600);
    } catch {
      selectNickname();
    }
  });
})();
