// Cookie notice banner (PR 185). Dismissed state lives in localStorage, not another
// cookie - base.html's inline script (_cookie_notice.html) already hides the banner
// synchronously for a returning dismissed visitor before first paint; this only wires
// up the "Понятно" click for a first-time visitor.
(() => {
  const STORAGE_KEY = "cookieNoticeDismissed";
  const banner = document.querySelector('[data-role="cookie-notice"]');
  const dismiss = document.querySelector('[data-role="cookie-notice-dismiss"]');
  if (!banner || !dismiss) return;

  dismiss.addEventListener("click", () => {
    banner.hidden = true;
    localStorage.setItem(STORAGE_KEY, "1");
  });
})();
