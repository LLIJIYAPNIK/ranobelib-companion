// The sidebar as a drawer on phones (<= 767px): the top bar's menu button opens the <nav>
// from the left (.sidebar--open, the mobile block of app.css); the scrim, ✕, Escape, a
// swipe to the left or following one of its links closes it. While it's open the page
// behind is inert and doesn't scroll, focus starts on the current section (or the first
// one) and Tab stays inside; closing gives focus back to the menu button. Crossing to the
// desktop width closes it, since there the same <nav> is the rail.
(() => {
  const sidebar = document.querySelector('[data-role="sidebar"]');
  const openButton = document.querySelector('[data-role="sidebar-drawer-open"]');
  const closeButton = document.querySelector('[data-role="sidebar-drawer-close"]');
  const scrim = document.querySelector('[data-role="sidebar-scrim"]');
  const main = document.querySelector(".main");
  if (!sidebar || !openButton || !scrim) return;

  const phone = window.matchMedia("(max-width: 767px)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const SWIPE_CLOSE = 60; // px to the left
  let hideScrimTimer = null;

  const isOpen = () => sidebar.classList.contains("sidebar--open");

  // What Tab may reach inside the drawer - not the top bar, which lives in the same <nav>
  // and is hidden while the drawer is open.
  function focusable() {
    return [...sidebar.querySelectorAll("a[href], button:not([disabled])")].filter(
      (el) => !el.closest(".sidebar__account") && el.offsetParent !== null
    );
  }

  function open() {
    if (isOpen() || !phone.matches) return;
    clearTimeout(hideScrimTimer);
    scrim.hidden = false;
    void scrim.offsetWidth; // let the scrim fade in from 0
    scrim.classList.add("sidebar-scrim--on");
    sidebar.classList.add("sidebar--open");
    document.documentElement.classList.add("sidebar-drawer-open");
    openButton.setAttribute("aria-expanded", "true");
    if (main) main.inert = true;
    const current = sidebar.querySelector('.sidebar__link[aria-current="page"]');
    (current || focusable()[0])?.focus({ preventScroll: true });
  }

  function close({ restoreFocus = true } = {}) {
    if (!isOpen()) return;
    sidebar.classList.remove("sidebar--open");
    scrim.classList.remove("sidebar-scrim--on");
    document.documentElement.classList.remove("sidebar-drawer-open");
    openButton.setAttribute("aria-expanded", "false");
    if (main) main.inert = false;
    hideScrimTimer = setTimeout(() => {
      scrim.hidden = true;
    }, reducedMotion.matches ? 0 : 260);
    if (restoreFocus) openButton.focus({ preventScroll: true });
  }

  openButton.addEventListener("click", open);
  closeButton?.addEventListener("click", () => close());
  scrim.addEventListener("click", () => close());

  // A section link: the page changes anyway, but a same-page link (or a cached back
  // navigation) would otherwise leave the drawer open.
  sidebar.addEventListener("click", (event) => {
    if (!isOpen()) return;
    const link = event.target.closest(".sidebar__nav a, .sidebar__me a, .sidebar__brand");
    if (link) close({ restoreFocus: false });
  });

  document.addEventListener("keydown", (event) => {
    if (!isOpen()) return;
    // The account menu opens the bottom sheet over the drawer - Escape and Tab are its.
    if (window.bottomSheet?.isOpen()) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
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
    } else if (!sidebar.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
  });

  // Swipe left on the drawer (or the scrim) to close - a mostly horizontal move only, so
  // scrolling a long drawer stays a scroll.
  let start = null;
  const onDown = (event) => {
    if (!isOpen() || event.isPrimary === false) return;
    start = { x: event.clientX, y: event.clientY };
  };
  const onUp = (event) => {
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    start = null;
    if (dx < -SWIPE_CLOSE && Math.abs(dx) > Math.abs(dy) * 1.5) close();
  };
  for (const el of [sidebar, scrim]) {
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", () => {
      start = null;
    });
  }

  phone.addEventListener("change", () => {
    if (!phone.matches) close({ restoreFocus: false });
  });

  // Back to a page from the bfcache with the drawer still drawn open.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) close({ restoreFocus: false });
  });
})();
