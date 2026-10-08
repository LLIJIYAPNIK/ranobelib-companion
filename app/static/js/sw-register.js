// PR 328: registers the service worker (scope "/": it's served from the site root) and
// offers its updates. A new version installs in the background and then waits - it's
// never swapped in silently in the middle of reading. On a later page load the waiting
// version is offered as «Доступна новая версия — Обновить»; only that tap activates it
// (SKIP_WAITING) and reloads this tab. «Позже» hides the offer for the rest of the tab's
// session; the browser switches versions by itself once every tab of the site is closed.
(() => {
  if (!("serviceWorker" in navigator)) return;

  const LATER_KEY = "swUpdateLater";
  let reloading = false;

  // Also fires when a first-ever worker takes control (clients.claim) - only reload after
  // the visitor asked for the update.
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) window.location.reload();
  });

  function postponed() {
    try {
      return sessionStorage.getItem(LATER_KEY) === "1";
    } catch {
      return false;
    }
  }

  function postpone() {
    try {
      sessionStorage.setItem(LATER_KEY, "1");
    } catch {
      // private mode: the offer just comes back on the next page
    }
  }

  function action(label, role, run) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "wn-toast__action";
    button.dataset.role = role;
    button.textContent = label;
    button.addEventListener("click", run);
    return button;
  }

  function offer(worker) {
    const toast = document.createElement("div");
    toast.className = "wn-toast";
    toast.dataset.role = "sw-update";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    const text = document.createElement("span");
    text.className = "wn-toast__text";
    text.textContent = "Доступна новая версия";
    const update = action("Обновить", "sw-update-apply", () => {
      reloading = true;
      update.disabled = true;
      worker.postMessage({ type: "SKIP_WAITING" });
    });
    const later = action("Позже", "sw-update-later", () => {
      postpone();
      toast.remove();
    });
    toast.append(text, update, later);
    document.body.append(toast);
  }

  navigator.serviceWorker
    .register("/service-worker.js", { scope: "/" })
    .then((registration) => {
      // No controller: this is the first worker, nothing old to replace.
      if (registration.waiting && navigator.serviceWorker.controller && !postponed()) {
        offer(registration.waiting);
      }
    })
    .catch(() => {});
})();
