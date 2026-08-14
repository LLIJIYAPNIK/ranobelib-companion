// Mobile "list first" flow for /settings/* (PR 104, доработка PR 89) - desktop keeps the
// nav column beside the content pane exactly as before, this only changes anything below
// the mobile breakpoint. Every /settings/<section> page still server-renders BOTH panes
// plus just that one section's own content - there's no shared route with all three
// sections' content at once - so a sessionStorage flag, set right before following a nav
// link, is what tells the *next* page load to show its content pane instead of the section
// list, since that's a full navigation, not a client-side swap. Absent (or after tapping
// the back link, which clears it) the list is shown instead, same as landing here fresh.
// Without JS, .settings-nav and .settings-content both stay visible per app.css's plain
// mobile fallback (the pre-PR-104 horizontal tab row) - this is a pure enhancement on top.
(() => {
  const MOBILE_BREAKPOINT = "(max-width: 640px)";
  const STORAGE_KEY = "settingsMobileDetail";

  const layout = document.querySelector('[data-role="settings-layout"]');
  const nav = document.querySelector('[data-role="settings-nav"]');
  const back = document.querySelector('[data-role="settings-back"]');
  if (!layout || !nav) return;

  const mobileQuery = window.matchMedia(MOBILE_BREAKPOINT);

  function render() {
    const mobile = mobileQuery.matches;
    const detail = mobile && sessionStorage.getItem(STORAGE_KEY) === "1";
    layout.classList.toggle("settings-layout--mobile-js", mobile);
    layout.classList.toggle("settings-layout--mobile-detail", detail);
    if (back) back.hidden = !detail;
  }

  nav.querySelectorAll(".settings-nav__link").forEach((link) => {
    link.addEventListener("click", () => sessionStorage.setItem(STORAGE_KEY, "1"));
  });

  back?.addEventListener("click", () => sessionStorage.removeItem(STORAGE_KEY));

  mobileQuery.addEventListener("change", render);
  render();
})();
