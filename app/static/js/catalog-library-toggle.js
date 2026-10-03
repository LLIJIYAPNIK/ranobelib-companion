// PR 298 (CatalogCard.dc.html / FeaturedCard.dc.html / Catalog handoff.md): the one
// action on a catalog card and a featured insert - «В библиотеку» / «В библиотеке».
// Not a favorite: a click adds the title to the library or, pressed again, takes it out
// again without asking - a title picked in the catalog has no progress yet, so there's
// nothing to lose (the library page's own removal always asks).
//
// The button flips at once (aria-pressed, its label, the «Библиотека» switch count),
// then POST /library/{slug}/add|remove with Accept: application/json. A non-JSON answer
// means the session ended and the request landed on /login - go there; a failure puts
// the button back as it was.
(() => {
  let toast = null;

  function showToast(message) {
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "wn-toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      document.body.append(toast);
    }
    const text = document.createElement("span");
    text.className = "wn-toast__text";
    text.textContent = message;
    toast.replaceChildren(text);
    toast.hidden = false;
    clearTimeout(toast.hideTimer);
    toast.hideTimer = setTimeout(() => {
      toast.hidden = true;
    }, 2600);
  }

  function setState(button, on) {
    const name = button.dataset.titleName || "";
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.setAttribute(
      "aria-label",
      on ? `«${name}» в библиотеке — убрать` : `Добавить «${name}» в библиотеку`
    );
    if (button.hasAttribute("title")) {
      button.title = on ? "В библиотеке — убрать" : "Добавить в библиотеку";
    }
    const label = button.querySelector('[data-role="catalog-library-label"]');
    if (label) label.textContent = on ? "В библиотеке" : "В библиотеку";
  }

  function bumpCount(delta) {
    const count = document.querySelector('[data-role="library-switch-count"]');
    if (count) count.textContent = String(Math.max(0, Number(count.textContent) + delta));
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest('[data-role="catalog-library-toggle"]');
    if (!button || button.getAttribute("aria-busy") === "true") return;
    const slug = button.dataset.slugUrl;
    const wasOn = button.getAttribute("aria-pressed") === "true";

    setState(button, !wasOn);
    bumpCount(wasOn ? -1 : 1);
    button.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(
        `/library/${encodeURIComponent(slug)}/${wasOn ? "remove" : "add"}`,
        { method: "POST", headers: { Accept: "application/json" } }
      );
      const json = (response.headers.get("content-type") || "").includes("application/json");
      if (response.ok && json) return;
      setState(button, wasOn);
      bumpCount(wasOn ? 1 : -1);
      if (response.ok && !json) {
        window.location.assign("/login");
        return;
      }
      showToast("Не удалось обновить библиотеку");
    } catch {
      setState(button, wasOn);
      bumpCount(wasOn ? 1 : -1);
      showToast("Не удалось обновить библиотеку");
    } finally {
      button.removeAttribute("aria-busy");
    }
  });
})();
