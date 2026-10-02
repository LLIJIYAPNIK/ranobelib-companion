// Horizontal ribbons (PR 282, Webnovells Mobile -> Библиотека; any element with
// data-ribbon): a touch-scrolled row - tabs today - that fades out at an edge only where
// there's more to scroll to (wn-ribbon--more-start / --more-end; the mask itself is CSS),
// and keeps its active item (aria-current / aria-selected) scrolled into view, so the
// current tab is never the one hidden off-screen.
(() => {
  const ribbons = document.querySelectorAll("[data-ribbon]");
  if (!ribbons.length) return;

  const EDGE = 4; // px of slack before an edge counts as "more to scroll"
  const PAD = 24; // breathing room left around the revealed item
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function updateFade(ribbon) {
    const start = ribbon.scrollLeft > EDGE;
    const end = ribbon.scrollLeft + ribbon.clientWidth < ribbon.scrollWidth - EDGE;
    ribbon.classList.toggle("wn-ribbon--more-start", start);
    ribbon.classList.toggle("wn-ribbon--more-end", end);
  }

  function reveal(ribbon, smooth) {
    const active = ribbon.querySelector('[aria-current="page"], [aria-selected="true"]');
    if (!active) return;
    const left = active.offsetLeft;
    const right = left + active.offsetWidth;
    let target = null;
    if (left - PAD < ribbon.scrollLeft) target = left - PAD;
    else if (right + PAD > ribbon.scrollLeft + ribbon.clientWidth) target = right + PAD - ribbon.clientWidth;
    if (target === null) return;
    ribbon.scrollTo({ left: Math.max(0, target), behavior: smooth && !reducedMotion.matches ? "smooth" : "auto" });
  }

  for (const ribbon of ribbons) {
    reveal(ribbon, false);
    updateFade(ribbon);
    ribbon.addEventListener("scroll", () => updateFade(ribbon), { passive: true });
    // Tabs that switch in place (no page load) move aria-selected - follow them.
    new MutationObserver(() => reveal(ribbon, true)).observe(ribbon, {
      subtree: true,
      attributeFilter: ["aria-selected", "aria-current"],
    });
  }

  window.addEventListener("resize", () => {
    for (const ribbon of ribbons) {
      reveal(ribbon, false);
      updateFade(ribbon);
    }
  });
})();
