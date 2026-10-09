// PR 337: the number on the installed app's icon (Badging API) - the same unread count as
// the sidebar bell's badge (notifications-panel.js reports every count it applies through
// update()). No bell on this page - a guest, notifications off or «Не беспокоить» - means
// nothing to show, so a badge left from earlier (another account, an expired session) is
// cleared. device-account.js clears it at logout too.
//
// Where the API is missing (most desktop browsers outside an installed app, iOS outside
// an app on the Home Screen) every call is a no-op; where it's there but not allowed, its
// rejection is swallowed. Without Web Push the badge only changes while a page of the site
// is open - it can't count notifications that arrive with the app closed.
(() => {
  if (window.appBadge) return;
  const supported = () => "setAppBadge" in navigator && "clearAppBadge" in navigator;
  let shown = null;

  function call(method, ...args) {
    try {
      Promise.resolve(navigator[method](...args)).catch(() => {});
    } catch {
      // a synchronous refusal: same as no badge at all
    }
  }

  function update(count) {
    if (!supported()) return;
    const value = Math.max(0, Math.floor(Number(count) || 0));
    if (value === shown) return;
    shown = value;
    if (value > 0) call("setAppBadge", value);
    else call("clearAppBadge");
  }

  function clear() {
    update(0);
  }

  window.appBadge = { supported, update, clear };

  // Pages with the bell set it from its first count; the rest clear what's left over.
  const start = () => {
    if (!document.querySelector('[data-role="notifications-trigger"]')) clear();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
