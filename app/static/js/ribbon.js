// Horizontal ribbons (PR 282, Webnovells Mobile -> Библиотека; any element with
// data-ribbon): a touch-scrolled row - tabs today - that fades out at an edge only where
// there's more to scroll to (wn-ribbon--more-start / --more-end; the mask itself is CSS),
// and keeps its active item (aria-current / aria-selected) scrolled into view, so the
// current tab is never the one hidden off-screen. PR 284 also uses the same ribbon for
// the profile heatmap: data-ribbon-current-week initially aligns its newest (rightmost)
// week, then leaves the user's scroll position alone.
(() => {
  const ribbons = document.querySelectorAll("[data-ribbon]");
  if (!ribbons.length) return;

  const EDGE = 4; // px of slack before an edge counts as "more to scroll"
  const PAD = 24; // breathing room left around the revealed item
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const phoneWidth = window.matchMedia("(max-width: 767px)");
  const currentWeekAligned = new WeakSet();

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

  function revealCurrentWeek(ribbon) {
    if (!ribbon.hasAttribute("data-ribbon-current-week")) return false;
    if (!phoneWidth.matches || currentWeekAligned.has(ribbon)) return true;
    ribbon.scrollLeft = Math.max(0, ribbon.scrollWidth - ribbon.clientWidth);
    currentWeekAligned.add(ribbon);
    return true;
  }

  for (const ribbon of ribbons) {
    // Wait for layout before measuring the year-wide heatmap. This is deliberately a
    // one-shot alignment: resize/scroll handlers below must not fight a manual swipe.
    requestAnimationFrame(() => {
      if (!revealCurrentWeek(ribbon)) reveal(ribbon, false);
      updateFade(ribbon);
    });
    ribbon.addEventListener("scroll", () => updateFade(ribbon), { passive: true });
    // Tabs that switch in place (no page load) move aria-selected - follow them.
    new MutationObserver(() => reveal(ribbon, true)).observe(ribbon, {
      subtree: true,
      attributeFilter: ["aria-selected", "aria-current"],
    });
  }

  window.addEventListener("resize", () => {
    for (const ribbon of ribbons) {
      if (!revealCurrentWeek(ribbon)) reveal(ribbon, false);
      updateFade(ribbon);
    }
  });
})();
