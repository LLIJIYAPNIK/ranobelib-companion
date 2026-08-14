// Reveals a floating "back to top" button once the visitor has scrolled some distance down
// the catalog (PR 15/24's infinite scroll can grow the page well past a single screen), and
// smooth-scrolls back to the top on click.
(() => {
  const button = document.querySelector('[data-role="catalog-back-to-top"]');
  if (!button) return;

  const REVEAL_DEPTH = 600;
  let ticking = false;

  function onScroll() {
    button.hidden = window.scrollY < REVEAL_DEPTH;
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
  onScroll();

  button.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();
