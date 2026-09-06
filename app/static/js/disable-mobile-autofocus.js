// PR 210: autofocus is fine on desktop (the cursor is just ready to type) but on mobile it
// pops the on-screen keyboard the instant the page loads, before the visitor has decided
// they want to type anything at all - the HTML autofocus attribute itself can't be
// conditional on a media query, so this blurs the field back off if the viewport is
// mobile-width (same breakpoint as .sidebar/the rest of app.css's mobile adaptations),
// rather than removing the attribute - desktop behavior (same markup, wider viewport)
// stays exactly as it was.
//
// Calling .blur() on the element right here, synchronously, does NOT work - verified with
// a real browser: the element isn't actually focused yet at this point (this script runs
// during parsing, right after the input itself), the browser applies `autofocus` as a
// separate, slightly later step, so an immediate .blur() targets an element that isn't
// focused yet and is a no-op; the native autofocus then fires afterwards and sticks.
// Registering a one-time "focus" listener here instead catches that later native focus
// exactly when it happens and blurs it right back off, regardless of the exact timing.
//
// Synchronous (no defer), not deferred - matching sidebar-expand-init.js's own reasoning:
// this listener has to be attached before the browser's own autofocus fires, and the
// element it targets already exists by the time this script (placed right after the
// form) runs.
if (window.matchMedia("(max-width: 640px)").matches) {
  const autofocused = document.querySelector("[autofocus]");
  if (autofocused) {
    autofocused.addEventListener("focus", () => autofocused.blur(), { once: true });
  }
}
