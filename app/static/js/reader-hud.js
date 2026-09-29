// Reader HUD (PR 254, Aurora Ink "05 Спецификация" -> Reader HUD). Replaces the old
// in-flow .reader-nav and PR 52's reveal-on-scroll-up copy (reader-scroll-nav.js) with a
// single fixed bar; data-role="reader-scroll-nav" stays on it. While the HUD is up,
// body.reader-hud-visible also shows the mobile bottom chapter bar and hides the 2px
// progress hairline (app.css).
//
// Shows: a center tap (Tap Focus dispatches "reader:toggle-hud"; in Scroll mode a
//   touch tap in the middle 24% of the screen does), scrolling up >= 24px, the tab
//   becoming visible again, keyboard focus moving into it.
// Hides: 3 s without interaction, scrolling down >= 48px, the next paragraph being
//   revealed ("reader:reveal") - but never while its «⋯» sheet is open or focus is
//   inside it.
// Progress: Tap Focus reports revealed/total ("reader:progress"); in Scroll mode it's how
//   far the chapter text has been scrolled.
// Keys: A - Aa (reading settings), T - table of contents, Esc - close the sheet or HUD
//   (never while typing in a field).
(() => {
  const hud = document.querySelector('[data-role="reader-scroll-nav"]');
  const content = document.querySelector('[data-role="chapter"]');
  if (!hud || !content) return;

  const body = document.body;
  const HIDE_AFTER_MS = 3000;
  const SHOW_SCROLL_UP = 24;
  const HIDE_SCROLL_DOWN = 48;
  const CENTER_ZONE = [0.28, 0.52];

  const sheet = document.querySelector('[data-role="reader-more-sheet"]');
  const moreTrigger = document.querySelector('[data-role="reader-more-trigger"]');
  const aa = document.querySelector('[data-role="reader-aa"]');
  const tocLink = document.querySelector('[data-role="reader-toc-link"]');
  const texts = document.querySelectorAll('[data-role="reader-progress-text"]');
  const fills = document.querySelectorAll('[data-role="reader-progress-fill"]');

  let hideTimer = null;

  function pinned() {
    return (sheet && sheet.open) || hud.contains(document.activeElement);
  }

  function show() {
    body.classList.add("reader-hud-visible");
    scheduleHide();
  }

  function hide() {
    if (pinned()) return;
    clearTimeout(hideTimer);
    body.classList.remove("reader-hud-visible");
  }

  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, HIDE_AFTER_MS);
  }

  function toggle() {
    if (body.classList.contains("reader-hud-visible")) hide();
    else show();
  }

  // --- progress --------------------------------------------------------------------
  function setProgress(fraction) {
    const percent = Math.round(Math.min(1, Math.max(0, fraction)) * 100);
    texts.forEach((el) => {
      el.textContent = `${percent}%`;
    });
    fills.forEach((el) => {
      el.style.width = `${percent}%`;
    });
  }

  const tapMode = content.classList.contains("reader-content--tap-to-read");

  // How far through the chapter text the reader has scrolled: 0% with its top at the
  // top of the screen, 100% once its end is in view (a text shorter than the screen is
  // read as soon as it's shown).
  function scrollProgress() {
    const rect = content.getBoundingClientRect();
    const travel = rect.height - window.innerHeight;
    if (travel <= 0) return 1;
    return -rect.top / travel;
  }

  document.addEventListener("reader:progress", (event) => {
    const { revealed, total } = event.detail || {};
    if (total > 0) setProgress(revealed / total);
  });
  if (tapMode) {
    // tap-to-read.js ran first and already reported its initial state before this
    // listener existed - read it back from the DOM once.
    const wraps = content.querySelectorAll(".reader-content__paragraph-wrap");
    const shown = content.querySelectorAll(
      ".reader-content__paragraph-wrap:not(.reader-content__paragraph--hidden)"
    );
    if (wraps.length) setProgress(shown.length / wraps.length);
  } else {
    setProgress(scrollProgress());
  }

  // --- scroll ------------------------------------------------------------------------
  // Distances are measured from where the current scroll direction started, so a slow
  // drift doesn't add up across direction changes.
  let lastY = window.scrollY;
  let anchorY = lastY;
  let direction = 0;
  let ticking = false;
  window.addEventListener(
    "scroll",
    () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        const y = window.scrollY;
        if (!tapMode) setProgress(scrollProgress());
        const nextDirection = Math.sign(y - lastY);
        if (nextDirection !== 0 && nextDirection !== direction) {
          direction = nextDirection;
          anchorY = lastY;
        }
        if (direction < 0 && anchorY - y >= SHOW_SCROLL_UP) show();
        if (direction > 0 && y - anchorY >= HIDE_SCROLL_DOWN) hide();
        lastY = y;
      });
    },
    { passive: true }
  );

  // --- taps --------------------------------------------------------------------------
  document.addEventListener("reader:toggle-hud", toggle);
  document.addEventListener("reader:reveal", hide);

  // Scroll mode: a touch tap in the middle of the page toggles the HUD. Mouse clicks
  // don't - on desktop the HUD comes back on scroll up or keyboard focus.
  let lastPointerType = "mouse";
  document.addEventListener("pointerdown", (event) => {
    lastPointerType = event.pointerType;
  });
  if (!tapMode) {
    document.addEventListener("click", (event) => {
      if (lastPointerType === "mouse") return;
      if (event.target.closest("a, button, input, textarea, select, label, img, sup, dialog, [role='toolbar'], .reader-hud-bottom, .paragraph-reactions, .paragraph-comments, .paragraph-menu__panel")) return;
      if (String(window.getSelection() || "")) return;
      const x = event.clientX / window.innerWidth;
      if (x >= CENTER_ZONE[0] && x < CENTER_ZONE[1]) toggle();
    });
  }

  // --- focus / visibility ------------------------------------------------------------
  hud.addEventListener("focusin", () => {
    body.classList.add("reader-hud-visible");
    clearTimeout(hideTimer);
  });
  hud.addEventListener("focusout", () => scheduleHide());
  hud.addEventListener("pointermove", () => scheduleHide());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") show();
  });

  // --- «⋯» sheet -----------------------------------------------------------------------
  if (sheet && moreTrigger) {
    moreTrigger.addEventListener("click", () => {
      sheet.showModal();
      clearTimeout(hideTimer);
    });
    const close = () => {
      if (!sheet.open) return;
      sheet.close();
    };
    sheet.querySelectorAll('[data-role="reader-sheet-close"]').forEach((b) => b.addEventListener("click", close));
    sheet.addEventListener("click", (event) => {
      if (event.target === sheet) close();
    });
    sheet.addEventListener("close", () => {
      moreTrigger.focus();
      scheduleHide();
    });
  }

  // --- keyboard ------------------------------------------------------------------------
  function typing(target) {
    return target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']");
  }

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return;
    const key = event.key.toLowerCase();
    if (key === "a" || key === "ф") {
      if (!aa) return;
      event.preventDefault();
      aa.click();
    } else if (key === "t" || key === "е") {
      if (!tocLink) return;
      event.preventDefault();
      tocLink.click();
    } else if (event.key === "Escape" && !(sheet && sheet.open)) {
      if (document.querySelector(".paragraph-menu__panel--open, .image-lightbox--open")) return;
      if (hud.contains(document.activeElement)) document.activeElement.blur();
      hide();
    }
  });

  // Desktop opens with the HUD up (READER-SCROLL); on a phone the text starts clean
  // (M-READER-SCROLL) and a center tap brings it in.
  if (!window.matchMedia("(max-width: 767px)").matches) show();
})();
