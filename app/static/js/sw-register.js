// PR 328: registers the service worker (scope "/": it's served from the site root).
(() => {
  if (!("serviceWorker" in navigator)) return;

  navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(() => {});
})();
