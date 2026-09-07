// PR 217: swipe gesture handling for the mobile bottom tab bar (.sidebar > .sidebar__link),
// extending PR 211's own swipe between the two library tabs to all five main navigation
// sections. Same pointerdown/pointerup approach as library-tabs-swipe.js, for the same
// reason (a mouse drag in a desktop-width emulator works too, and a real vertical scroll
// never delivers "pointerup" so this never has to fight page scrolling).
//
// Listens on .main (the shared content container, base.html) rather than the narrow .sidebar
// bar itself, which is awkward to swipe on directly.
(() => {
  const main = document.querySelector(".main");
  if (!main) return;

  const mobileQuery = window.matchMedia("(max-width: 640px)");
  const MIN_DISTANCE = 60;
  // Same system-gesture reasoning as library-tabs-swipe.js's own EDGE_EXCLUSION.
  const EDGE_EXCLUSION = 24;

  let pointerId = null;
  let startX = 0;
  let startY = 0;

  main.addEventListener("pointerdown", (event) => {
    if (!mobileQuery.matches) return;
    if (event.clientX < EDGE_EXCLUSION || event.clientX > window.innerWidth - EDGE_EXCLUSION) return;
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
  });

  main.addEventListener("pointerup", (event) => {
    if (pointerId === null || event.pointerId !== pointerId) return;
    pointerId = null;
    if (!mobileQuery.matches) return;

    const deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    // A horizontal swipe: far enough on the X axis, and X dominates Y - a vertical
    // scroll flick would fail this even in the rare case it still delivers "pointerup".
    if (Math.abs(deltaX) < MIN_DISTANCE || Math.abs(deltaX) < Math.abs(deltaY)) return;

    // Navigating to the neighboring section is added in the next commit.
  });
})();
