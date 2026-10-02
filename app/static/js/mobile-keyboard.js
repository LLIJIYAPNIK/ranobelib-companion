// PR 279 (Webnovells Mobile -> Mobile handoff.md, Общие правила): the bottom navigation
// steps aside while the on-screen keyboard is open, so it doesn't ride up on top of the
// keyboard and cover the field being typed into. There's no keyboard event - the
// keyboard counts as open when the visual viewport is 140px+ shorter than the window
// (html.keyboard-open, the CSS hides the bar). Browsers without visualViewport keep the
// bar.
(() => {
  const viewport = window.visualViewport;
  if (!viewport) return;

  const KEYBOARD_MIN = 140;
  const root = document.documentElement;

  function update() {
    root.classList.toggle("keyboard-open", window.innerHeight - viewport.height > KEYBOARD_MIN);
  }

  viewport.addEventListener("resize", update);
  window.addEventListener("resize", update);
  update();
})();
