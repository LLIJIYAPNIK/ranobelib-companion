// PR 211: swipe gesture handling for .library-tabs-content (_library_tabs.html's
// "Читаю"/"Все тайтлы" tabs). Uses pointerdown/pointerup (not touchstart/touchend) so the
// same code path also works for a mouse drag in a desktop-width devtools emulator - the
// same pattern image-lightbox.js already uses for its own drag-to-pan (PR 204).
// Deliberately does not call setPointerCapture() or preventDefault(): a genuine vertical
// scroll of the title list turns into a real touch-scroll gesture, which the browser
// reports as "pointercancel" instead of "pointerup" - the handler below simply never fires
// in that case, so it never has to fight the page's own scrolling.
//
// Mobile-only (same breakpoint as the rest of the mobile adaptation, app.css) - these two
// tabs render identically above and below it (PR 47), but on desktop a horizontal swipe
// shortcut isn't needed and would only risk fighting normal horizontal scrolling of some
// other wide element on the page.
(() => {
  const content = document.querySelector(".library-tabs-content");
  const tabLinks = [...document.querySelectorAll(".library-tabs__link")];
  if (!content || tabLinks.length < 2) return;

  const mobileQuery = window.matchMedia("(max-width: 640px)");
  const MIN_DISTANCE = 60;
  // A swipe starting right at the edge of the screen is left alone - some mobile browsers
  // reserve that strip for their own "swipe back" system gesture, and fighting over the
  // same gesture there would be worse than just not handling it.
  const EDGE_EXCLUSION = 24;

  let pointerId = null;
  let startX = 0;
  let startY = 0;

  content.addEventListener("pointerdown", (event) => {
    if (!mobileQuery.matches) return;
    if (event.clientX < EDGE_EXCLUSION || event.clientX > window.innerWidth - EDGE_EXCLUSION) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
  });

  content.addEventListener("pointerup", (event) => {
    if (pointerId === null || event.pointerId !== pointerId) return;
    pointerId = null;
    if (!mobileQuery.matches) return;

    const deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    // A horizontal swipe: far enough on the X axis, and X dominates Y - a vertical
    // scroll flick would fail this even in the rare case it still delivers "pointerup".
    if (Math.abs(deltaX) < MIN_DISTANCE || Math.abs(deltaX) < Math.abs(deltaY)) return;

    const activeIndex = tabLinks.findIndex((link) =>
      link.classList.contains("library-tabs__link--active")
    );
    if (activeIndex === -1) return;

    // Swipe left ("Читаю" → "Все тайтлы") moves to the next tab link, swipe right moves
    // back to the previous one - there's nothing to move to past either end (missing
    // index just means `target` below is undefined).
    const target = tabLinks[deltaX < 0 ? activeIndex + 1 : activeIndex - 1];
    if (target) window.location.href = target.href;
  });
})();
