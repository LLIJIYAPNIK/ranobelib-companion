// Reveals a fixed overlay copy of the chapter nav (back link, heading, prev/next -
// app/templates/chapter.html) on any upward scroll, and hides it again on downward
// scroll (PR 52) - lets a reader mid-chapter pull the controls into view without
// scrolling all the way back to the top. Suppressed near the very top of the page, where
// the original in-flow .reader-nav is already visible on its own.
(() => {
  const nav = document.querySelector('[data-role="reader-scroll-nav"]');
  const main = document.querySelector(".main");
  if (!nav || !main) return;

  const REVEAL_DELTA = 8;
  // Below this scrollY, the in-flow .reader-nav at the top of the page is still (at
  // least partly) visible on its own - showing the overlay too would just duplicate it.
  const TOP_GUARD = 120;

  let lastY = window.scrollY;
  let ticking = false;

  // PR 161: now that .reader-scroll-nav is `position: fixed` (not `sticky` - see
  // app.css), it no longer automatically stays contained within .main's own box, so its
  // left/width are tracked here from .main's live rect instead, to still never overlap
  // the sidebar as it collapses/expands (PR 39/48/49) or the viewport resizes.
  function positionNav() {
    const rect = main.getBoundingClientRect();
    nav.style.left = `${rect.left}px`;
    nav.style.width = `${rect.width}px`;
  }

  function onScroll() {
    const y = window.scrollY;
    const delta = y - lastY;

    if (y <= TOP_GUARD) {
      nav.classList.remove("reader-scroll-nav--visible");
    } else if (delta < -REVEAL_DELTA) {
      nav.classList.add("reader-scroll-nav--visible");
    } else if (delta > REVEAL_DELTA) {
      nav.classList.remove("reader-scroll-nav--visible");
    }

    positionNav();
    lastY = y;
    ticking = false;
  }

  positionNav();

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
  window.addEventListener("resize", positionNav);
})();
