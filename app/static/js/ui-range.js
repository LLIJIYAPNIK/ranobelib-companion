// Aurora Ink range fill (PR 248): WebKit has no pseudo-element for the filled part of
// an <input type="range">, so .ui-range draws it as a gradient stopping at
// --ui-range-fill - this keeps that property equal to the input's current position,
// on load and on every input. Firefox paints the fill itself (::-moz-range-progress)
// and ignores the property. Without JS the slider still works, just without a fill.
// Included per page, next to the markup that uses .ui-range (same as custom-dropdown.js).
(() => {
  function sync(input) {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const percent = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
    input.style.setProperty("--ui-range-fill", `${percent}%`);
  }

  document.querySelectorAll("input.ui-range").forEach(sync);
  document.addEventListener("input", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.matches(".ui-range")) {
      sync(event.target);
    }
  });
})();
