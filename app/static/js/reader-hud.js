// Reader HUD (PR 254, Aurora Ink "05 Спецификация" -> Reader HUD). Replaces the old
// in-flow .reader-nav and PR 52's reveal-on-scroll-up copy (reader-scroll-nav.js) with a
// single fixed bar; data-role="reader-scroll-nav" stays on it. Reader «Корешок»: the bar
// is a dock at the foot of the screen (app.css), and instead of a running percent it says
// what is left - minutes in Scroll mode, paragraphs and minutes in Tap Focus.
//
// Shows: a center tap (Tap Focus dispatches "reader:toggle-hud"; in Scroll mode a
//   touch tap in the middle 24% of the screen does), scrolling up >= 24px, the tab
//   becoming visible again, keyboard focus moving into it.
// Hides: 3 s without interaction, scrolling down >= 48px, the next paragraph being
//   revealed ("reader:reveal") - but never while its «⋯» sheet is open or focus is
//   inside it.
// Progress: Tap Focus reports revealed/total ("reader:progress"); in Scroll mode it's how
//   far the chapter text has been scrolled.
// Keys: A - Aa (reading settings), T - the volume's chapters, Esc - close the menu or HUD
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

  let hideTimer = null;

  function sheetOpen() {
    return window.bottomSheet?.isOpen() === true;
  }

  function pinned() {
    return (
      document.querySelector("dialog[open]") !== null || sheetOpen() || hud.contains(document.activeElement)
    );
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
  // Minutes left: the words not read yet at the reader's own speed (readingSpeedWpm,
  // the speed test - PR 77/78), 200 wpm until it's measured, same as tap-to-read.js.
  const DEFAULT_WPM = 200;
  const timeLeft = document.querySelectorAll('[data-role="reader-time-left"]');
  const timeTotal = document.querySelector('[data-role="reader-time-total"]');
  const words = (content.textContent.match(/\S+/g) || []).length;
  function wpm() {
    try {
      const value = Number(JSON.parse(localStorage.getItem("readerSettings") || "{}").readingSpeedWpm);
      return value > 0 ? value : DEFAULT_WPM;
    } catch {
      return DEFAULT_WPM;
    }
  }
  const speed = wpm();
  function minutes(count) {
    return Math.max(1, Math.round(count / speed));
  }
  if (timeTotal && words) {
    timeTotal.textContent = `≈ ${minutes(words)} мин`;
    timeTotal.hidden = false;
  }

  function setProgress(fraction) {
    const share = Math.min(1, Math.max(0, fraction));
    timeLeft.forEach((el) => {
      el.textContent = leftLabel(share);
    });
  }

  // What is left, in the HUD only - it shows when the reader asks for it (scrolling up,
  // a tap in the middle), not under every paragraph. Tap Focus counts the paragraphs
  // still hidden, Scroll mode the share of the text below the screen.
  function paragraphsWord(count) {
    const tens = count % 100;
    const ones = count % 10;
    if (tens >= 11 && tens <= 14) return "абзацев";
    if (ones === 1) return "абзац";
    if (ones >= 2 && ones <= 4) return "абзаца";
    return "абзацев";
  }
  function leftLabel(share) {
    if (!words) return "";
    if (content.classList.contains("reader-content--tap-to-read")) {
      const hidden = content.querySelectorAll(".reader-content__paragraph--hidden");
      if (!hidden.length) return "глава дочитана";
      let left = 0;
      hidden.forEach((wrap) => {
        left += (wrap.textContent.match(/\S+/g) || []).length;
      });
      return `Ещё ${hidden.length} ${paragraphsWord(hidden.length)} · ≈ ${minutes(left)} мин`;
    }
    const left = Math.round(words * (1 - share));
    return left < 30 ? "глава дочитана" : `≈ ${minutes(left)} мин до конца`;
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
      if (event.target.closest("a, button, input, textarea, select, label, img, sup, dialog, [role='dialog'], [role='toolbar'], .paragraph-reactions, .paragraph-comments, .paragraph-menu__panel, .bottom-sheet")) return;
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

  // --- menus over the dock ---------------------------------------------------------------
  // «Скачать главу» and the volume's chapters open as menus right over the button that
  // opened them, not as a sheet over the whole screen: their bottom edge 10px above it,
  // lined up with its right edge (download) or its left edge (chapters), kept on screen.
  function placeOver(menu, opener, align) {
    const rect = opener.getBoundingClientRect();
    const margin = 12;
    menu.style.setProperty("--menu-bottom", `${Math.max(margin, window.innerHeight - rect.top + 10)}px`);
    if (align === "right") {
      menu.style.setProperty("--menu-right", `${Math.max(margin, window.innerWidth - rect.right)}px`);
      menu.style.removeProperty("--menu-left");
    } else {
      menu.style.setProperty("--menu-left", `${Math.max(margin, rect.left)}px`);
      menu.style.removeProperty("--menu-right");
    }
  }

  const toc = document.querySelector('[data-role="reader-toc"]');
  if (toc && tocLink) {
    tocLink.setAttribute("aria-haspopup", "dialog");
    tocLink.addEventListener("click", (event) => {
      event.preventDefault();
      if (toc.open) {
        toc.close();
        return;
      }
      body.classList.add("reader-hud-visible");
      clearTimeout(hideTimer);
      placeOver(toc, tocLink, "left");
      toc.showModal();
      tocLink.setAttribute("aria-expanded", "true");
      const current = toc.querySelector('[aria-current="page"]');
      current?.scrollIntoView({ block: "center" });
      current?.focus({ preventScroll: true });
    });
    toc.querySelector('[data-role="reader-toc-close"]')?.addEventListener("click", () => toc.close());
    toc.addEventListener("click", (event) => {
      if (event.target === toc) toc.close();
    });
    toc.addEventListener("close", () => {
      tocLink.setAttribute("aria-expanded", "false");
      scheduleHide();
    });
  }

  if (sheet && moreTrigger) {
    moreTrigger.addEventListener("click", () => {
      placeOver(sheet, moreTrigger, "right");
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

  // --- Aa panel (PR 255) ----------------------------------------------------------------
  // Opened from the HUD's Aa (a plain /settings/reading link without JS). Changes apply
  // as they're made (reader-settings.js); switching Scroll <-> Tap Focus reloads the
  // chapter, since tap-to-read.js sets the text up once on load.
  // PR 321: on phones the controls open in the shared bottom sheet (drag to close, focus
  // trap, background lock - bottom-sheet.js); the <dialog> is the desktop popover.
  const aaPanel = document.querySelector('[data-role="reader-aa-panel"]');
  const aaContent = aaPanel?.querySelector('[data-role="reader-aa-content"]');
  const phone = window.matchMedia("(max-width: 767px)");
  if (aa && aaPanel) {
    aa.setAttribute("aria-haspopup", "dialog");
    const aaInSheet = () => sheetOpen() && aaContent?.closest('[data-role="bottom-sheet"]') != null;
    aa.addEventListener("click", (event) => {
      event.preventDefault();
      if (aaPanel.open) {
        aaPanel.close();
        return;
      }
      if (aaInSheet()) {
        window.bottomSheet.close(); // its onClose resets aria-expanded
        return;
      }
      body.classList.add("reader-hud-visible");
      clearTimeout(hideTimer);
      aa.setAttribute("aria-expanded", "true");
      if (phone.matches && window.bottomSheet && aaContent) {
        window.bottomSheet.open({
          title: "Настройки чтения",
          content: aaContent,
          opener: aa,
          onClose: () => {
            aa.setAttribute("aria-expanded", "false");
            scheduleHide();
          },
        });
        return;
      }
      aaPanel.showModal();
    });
    aaPanel.querySelector('[data-role="reader-aa-close"]')?.addEventListener("click", () => aaPanel.close());
    aaPanel.addEventListener("click", (event) => {
      if (event.target === aaPanel) aaPanel.close();
    });
    aaPanel.addEventListener("close", () => {
      aa.setAttribute("aria-expanded", "false");
      aa.focus();
      scheduleHide();
    });
    document.addEventListener("reader-settings:change", (event) => {
      if (event.detail?.modeChanged) location.reload();
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
    } else if (event.key === "Escape" && !document.querySelector("dialog[open]") && !sheetOpen()) {
      if (document.querySelector(".paragraph-menu__panel--open, .image-lightbox--open")) return;
      if (hud.contains(document.activeElement)) document.activeElement.blur();
      hide();
    }
  });

  // Desktop opens with the HUD up (READER-SCROLL); on a phone the text starts clean
  // (M-READER-SCROLL) and a center tap brings it in.
  if (!window.matchMedia("(max-width: 767px)").matches) show();
})();
