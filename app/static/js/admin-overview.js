// PR 345: «Обзор»'s loading state. The period, metric and comparison switches are plain
// links - the server renders the new numbers - so between the click and the next page
// the current numbers are marked as stale: the page is aria-busy, its data dims and the
// switch shows a spinner. A page restored from the back/forward cache drops the mark.
(() => {
  const page = document.querySelector("[data-admin-overview]");
  if (!page) return;

  const setBusy = (busy, link) => {
    page.toggleAttribute("data-loading", busy);
    page.setAttribute("aria-busy", String(busy));
    page.querySelectorAll("[data-overview-pending]").forEach((el) => {
      el.removeAttribute("data-overview-pending");
    });
    if (busy && link) link.setAttribute("data-overview-pending", "");
  };

  page.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-overview-nav]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.getAttribute("aria-current")) return; // already shown - nothing to load
    setBusy(true, link);
  });

  window.addEventListener("pageshow", () => setBusy(false));
})();
