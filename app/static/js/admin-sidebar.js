// PR 341: the admin sidebar as a slide-out panel at <=1024px (RanobeLib Admin.dc.html's
// burger + scrim). Wider screens show it in place and never run this. Opens from the top
// bar's menu button; the close button, the scrim and Escape close it, focus moves into
// the panel and back to the button, and Tab stays inside while it's open.
(() => {
  const side = document.querySelector('[data-role="admin-sidebar"]');
  const opener = document.querySelector('[data-role="admin-nav-open"]');
  const scrim = document.querySelector('[data-role="admin-nav-scrim"]');
  const closer = side && side.querySelector('[data-role="admin-nav-close"]');
  if (!side || !opener || !scrim || !closer) return;

  const drawer = window.matchMedia("(max-width: 1024px)");
  const isOpen = () => side.hasAttribute("data-open");

  const setOpen = (open, returnFocus) => {
    side.toggleAttribute("data-open", open);
    scrim.hidden = !open;
    document.body.classList.toggle("wn-admin--nav-open", open);
    opener.setAttribute("aria-expanded", String(open));
    if (open) {
      (side.querySelector('[aria-current="page"]') || closer).focus();
    } else if (returnFocus) {
      opener.focus();
    }
  };

  const focusable = () =>
    [...side.querySelectorAll("a[href], button:not([disabled])")].filter(
      (el) => el.offsetParent !== null,
    );

  opener.addEventListener("click", () => setOpen(true));
  closer.addEventListener("click", () => setOpen(false, true));
  scrim.addEventListener("click", () => setOpen(false, true));

  document.addEventListener("keydown", (event) => {
    if (!isOpen()) return;
    if (event.key === "Escape") {
      setOpen(false, true);
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusable();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (!side.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
  });

  // Rotating/resizing past 1024px leaves the in-place sidebar - nothing to close.
  drawer.addEventListener("change", (event) => {
    if (!event.matches && isOpen()) setOpen(false);
  });
})();
