// Mobile top strip (PR 71's .sidebar__account, reshaped by PR 249 / Aurora Ink): the
// strip stays transparent over the top of the page and gets its --bg-page 96% background
// only once the page has scrolled 8px ("05 Спецификация" -> Top strip).
//
// Until PR 249 this script reparented the Settings link and the notifications bell into
// .sidebar__account-actions on mobile (PR 213). Settings lives in the Account hub now
// and the bell is rendered inside .sidebar__account from the start, so there is nothing
// left to move. Desktop never sees the class matter - the strip is mobile-only CSS.
(() => {
  const strip = document.querySelector(".sidebar__account");
  if (!strip) return;

  const THRESHOLD = 8;
  let scheduled = false;

  function update() {
    scheduled = false;
    strip.classList.toggle("sidebar__account--scrolled", window.scrollY > THRESHOLD);
  }

  window.addEventListener(
    "scroll",
    () => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(update);
    },
    { passive: true }
  );
  update();

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
