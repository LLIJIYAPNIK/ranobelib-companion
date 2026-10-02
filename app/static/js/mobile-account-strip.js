// Mobile header (PR 71's .sidebar__account, reshaped by PR 249 / Aurora Ink and PR 279 /
// Webnovells Mobile): the back button a page can put in place of the logo.
//
// Until PR 279 this script also gave the strip its background once the page had scrolled
// 8px; the Webnovells Mobile header is opaque from the first pixel, so that part is gone.
// Until PR 249 it reparented the Settings link and the notifications bell into
// .sidebar__account-actions on mobile (PR 213); both are rendered in place now.
(() => {
  const strip = document.querySelector(".sidebar__account");
  if (!strip) return;

  // PR 253: the strip's back button (base.html, strip_back_href) - a real history step
  // when we came from a page of this site, its fallback href otherwise (a shared link,
  // a new tab).
  const back = strip.querySelector('[data-role="strip-back"]');
  back?.addEventListener("click", (event) => {
    let sameOrigin = false;
    try {
      sameOrigin = document.referrer && new URL(document.referrer).origin === location.origin;
    } catch {
      sameOrigin = false;
    }
    if (!sameOrigin || history.length < 2) return;
    event.preventDefault();
    history.back();
  });
})();
