// PR 211: swipe gesture handling for .library-tabs-content (_library_tabs.html's
// "Читаю"/"Все тайтлы" tabs). Uses pointerdown/pointerup (not touchstart/touchend) so the
// same code path also works for a mouse drag in a desktop-width devtools emulator - the
// same pattern image-lightbox.js already uses for its own drag-to-pan (PR 204).
// Deliberately does not call setPointerCapture() or preventDefault(): a genuine vertical
// scroll of the title list turns into a real touch-scroll gesture, which the browser
// reports as "pointercancel" instead of "pointerup" - the handler below simply never fires
// in that case, so it never has to fight the page's own scrolling.
(() => {
  const content = document.querySelector(".library-tabs-content");
  if (!content) return;

  const MIN_DISTANCE = 60;

  let pointerId = null;
  let startX = 0;
  let startY = 0;

  content.addEventListener("pointerdown", (event) => {
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
  });

  content.addEventListener("pointerup", (event) => {
    if (pointerId === null || event.pointerId !== pointerId) return;
    pointerId = null;

    const deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    // A horizontal swipe: far enough on the X axis, and X dominates Y - a vertical
    // scroll flick would fail this even in the rare case it still delivers "pointerup".
    if (Math.abs(deltaX) < MIN_DISTANCE || Math.abs(deltaX) < Math.abs(deltaY)) return;

    const direction = deltaX < 0 ? "left" : "right";
    content.dispatchEvent(new CustomEvent("library-tabs-swipe", { detail: { direction } }));
  });
})();
