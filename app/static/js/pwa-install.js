// PR 327: installing the site as an app - no banners. Loaded on every page so the browser's
// own install prompt is always caught and held back (preventDefault: no Chrome
// mini-infobar); it's only ever offered from the «Приложение» card in Settings → Чтение:
// - Chromium-type browsers: the card's «Установить приложение» button replays the held
//   prompt;
// - iOS Safari, which has no prompt: a hint, «Поделиться» → «На экран „Домой"»;
// - already running installed: the card says so, no button;
// - any other browser: the card stays hidden - nothing to offer there.
(() => {
  let deferredPrompt = null;

  const card = document.querySelector('[data-role="pwa-install"]');
  const text = card?.querySelector('[data-role="pwa-install-text"]');
  const button = card?.querySelector('[data-role="pwa-install-button"]');

  const standalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const ios =
    /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function show(message, withButton) {
    if (!card) return;
    if (message) text.textContent = message;
    button.hidden = !withButton;
    card.hidden = false;
  }

  function render() {
    if (!card) return;
    if (standalone()) show("Webnovells уже установлен и открыт как приложение.", false);
    else if (deferredPrompt) show(null, true);
    else if (ios) {
      show("В Safari нажмите «Поделиться», затем «На экран „Домой“».", false);
    }
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    render();
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    show("Готово — Webnovells установлен.", false);
  });

  button?.addEventListener("click", async () => {
    if (!deferredPrompt) return;
    const prompt = deferredPrompt;
    deferredPrompt = null;
    button.hidden = true;
    prompt.prompt();
    const { outcome } = await prompt.userChoice;
    // Declined: Chrome may offer the prompt again later (a new beforeinstallprompt).
    if (outcome !== "accepted") render();
  });

  render();
})();
