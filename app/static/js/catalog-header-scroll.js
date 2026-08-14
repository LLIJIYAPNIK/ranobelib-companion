// Hides the catalog's .header (search bar, sort, "Фильтры" toggle) on scroll-down and
// reveals it again on scroll-up (PR 101) - same direction-detection logic as
// reader-scroll-nav.js (PR 52), inverted: there the overlay is hidden by default because a
// separate in-flow nav is already visible, but the catalog only has this one header, so it
// ships visible by default (app.css has no transform without --hidden) and this script only
// ever adds/removes that modifier, never a --visible one.
(() => {
  const header = document.querySelector('[data-role="catalog-scroll-header"]');
  if (!header) return;

  const REVEAL_DELTA = 8;
  // Below this scrollY, the header is still at (or near) its natural resting position at
  // the top of the page - force it visible so scrolling back to the top never leaves it
  // stuck hidden from a downward scroll made deeper in the page.
  const TOP_GUARD = 120;

  let lastY = window.scrollY;
  let ticking = false;

  function onScroll() {
    const y = window.scrollY;
    const delta = y - lastY;

    if (y <= TOP_GUARD) {
      header.classList.remove("header--hidden");
    } else if (delta > REVEAL_DELTA) {
      header.classList.add("header--hidden");
    } else if (delta < -REVEAL_DELTA) {
      header.classList.remove("header--hidden");
    }

    lastY = y;
    ticking = false;
  }

  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        requestAnimationFrame(onScroll);
        ticking = true;
      }
    },
    { passive: true }
  );
})();
