// The one bottom sheet (PR 279, Webnovells Mobile -> Mobile handoff.md, "Bottom sheet
// (одна реализация для всех)"). base.html renders a single empty sheet; whoever needs a
// sheet hands it a content element, which is moved into the sheet body while open and
// put back where it was (and re-hidden, if it was) on close:
//
//   window.bottomSheet.open({ title, content, opener, onClose })
//   window.bottomSheet.close()
//
// or, with no JS of your own, a button with data-bottom-sheet="<id of a hidden element>"
// (title from the element's data-bottom-sheet-title). Inside the content,
// data-bottom-sheet-close closes the sheet and data-autofocus picks the first focus.
//
// Drag: the head (grabber + title) via Pointer Events + pointer capture, never from its
// buttons; the body via touch events, taken over only when it starts at scrollTop <= 0
// and moves down more than 6px - otherwise it's an ordinary scroll. Released past
// min(160px, 33% of the height) or faster than 0.55 px/ms over the last 100ms (a pause of
// more than 80ms before release zeroes the speed), it closes; otherwise it springs back
// in 220ms. prefers-reduced-motion drops every animation.
(() => {
  const root = document.querySelector('[data-role="bottom-sheet"]');
  if (!root) return;

  const backdrop = root.querySelector('[data-role="bottom-sheet-backdrop"]');
  const panel = root.querySelector('[data-role="bottom-sheet-panel"]');
  const head = root.querySelector('[data-role="bottom-sheet-head"]');
  const titleEl = root.querySelector('[data-role="bottom-sheet-title"]');
  const body = root.querySelector('[data-role="bottom-sheet-body"]');
  const appRoot = document.querySelector(".app-shell");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const CLOSE_DISTANCE = 160;
  const CLOSE_SHARE = 0.33;
  const CLOSE_SPEED = 0.55; // px/ms
  const SPEED_WINDOW = 100; // ms
  const SPEED_PAUSE = 80; // ms
  const BODY_SLOP = 6; // px
  const OPEN_MS = 300;
  const CLOSE_MS = 220; // also the spring back
  const BACKDROP_IN_MS = 240;

  let current = null; // { content, placeholder, wasHidden, opener, onClose }
  let closing = false;
  let drag = null;
  let bodyTouch = null;
  let savedScrollY = 0;

  function animate(transform, opacity, panelTransition, backdropMs) {
    const off = reducedMotion.matches;
    panel.style.transition = off ? "none" : `transform ${panelTransition}`;
    backdrop.style.transition = off ? "none" : `opacity ${backdropMs}ms ease`;
    panel.style.transform = transform;
    backdrop.style.opacity = opacity;
  }

  function fitToViewport() {
    const height = window.visualViewport ? window.visualViewport.height : window.innerHeight;
    panel.style.maxHeight = `calc(${Math.round(height)}px - 24px - env(safe-area-inset-top, 0px))`;
  }

  // Background lock: overflow:hidden on <html> plus inert on the app; iOS Safari still
  // scrolls the page under that, so body is pinned in place too and the scroll position
  // restored on unlock.
  function lockBackground() {
    savedScrollY = window.scrollY;
    document.documentElement.classList.add("bottom-sheet-lock");
    document.body.style.top = `-${savedScrollY}px`;
    if (appRoot) appRoot.inert = true;
  }

  function unlockBackground() {
    document.documentElement.classList.remove("bottom-sheet-lock");
    document.body.style.top = "";
    window.scrollTo(0, savedScrollY);
    if (appRoot) appRoot.inert = false;
  }

  function mount(options) {
    const content = options.content;
    const placeholder = document.createComment("bottom-sheet");
    content.before(placeholder);
    const wasHidden = content.hidden;
    content.hidden = false;
    body.appendChild(content);
    titleEl.textContent = options.title || "";
    current = { content, placeholder, wasHidden, opener: options.opener, onClose: options.onClose };
  }

  function unmount() {
    const { content, placeholder, wasHidden, onClose } = current;
    content.hidden = wasHidden;
    placeholder.replaceWith(content);
    current = null;
    onClose?.();
  }

  function open(options) {
    if (!options?.content) return;
    if (current) {
      // Already open: swap the content in place, keep the original opener for focus.
      const opener = current.opener;
      unmount();
      mount({ ...options, opener });
      focusFirst();
      return;
    }
    mount({ ...options, opener: options.opener || document.activeElement });
    root.hidden = false;
    fitToViewport();
    lockBackground();
    body.scrollTop = 0;
    panel.style.transition = "none";
    backdrop.style.transition = "none";
    panel.style.transform = "translateY(100%)";
    backdrop.style.opacity = "0";
    void panel.offsetHeight;
    animate("translateY(0)", "1", `${OPEN_MS}ms cubic-bezier(.22,.9,.24,1)`, BACKDROP_IN_MS);
    requestAnimationFrame(focusFirst);
  }

  function focusFirst() {
    const target =
      panel.querySelector("[data-autofocus]") ||
      panel.querySelector('[data-role="bottom-sheet-close"]');
    target?.focus({ preventScroll: true });
  }

  function close() {
    if (!current || closing) return;
    closing = true;
    drag = null;
    bodyTouch = null;
    animate("translateY(100%)", "0", `${CLOSE_MS}ms cubic-bezier(.4,0,1,1)`, CLOSE_MS);
    window.setTimeout(
      () => {
        closing = false;
        root.hidden = true;
        unlockBackground();
        const opener = current.opener;
        unmount();
        if (opener?.focus && document.contains(opener)) opener.focus({ preventScroll: true });
      },
      reducedMotion.matches ? 0 : CLOSE_MS
    );
  }

  function springBack() {
    animate("translateY(0)", "1", `${CLOSE_MS}ms cubic-bezier(.22,.9,.24,1)`, CLOSE_MS);
  }

  function dragStart(y) {
    drag = { y0: y, dy: 0, height: panel.offsetHeight, samples: [{ t: performance.now(), y }] };
    panel.style.transition = "none";
    backdrop.style.transition = "none";
  }

  function dragMove(y) {
    if (!drag) return;
    drag.dy = Math.max(0, y - drag.y0);
    drag.samples.push({ t: performance.now(), y });
    if (drag.samples.length > 8) drag.samples.shift();
    panel.style.transform = `translateY(${drag.dy}px)`;
    backdrop.style.opacity = String(Math.max(0, 1 - drag.dy / drag.height));
  }

  function dragEnd() {
    const d = drag;
    drag = null;
    if (!d) return;
    const now = performance.now();
    const last = d.samples[d.samples.length - 1];
    const first = d.samples.find((s) => now - s.t <= SPEED_WINDOW) || d.samples[0];
    const speed = now - last.t > SPEED_PAUSE ? 0 : (last.y - first.y) / Math.max(16, last.t - first.t);
    const far = d.dy > Math.min(CLOSE_DISTANCE, d.height * CLOSE_SHARE);
    if (far || (speed > CLOSE_SPEED && d.dy > 16)) close();
    else springBack();
  }

  head.addEventListener("pointerdown", (event) => {
    if (!current || closing || event.target.closest("button, a")) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    dragStart(event.clientY);
    try {
      head.setPointerCapture(event.pointerId);
    } catch {
      // A pointer that's already gone can't be captured - the drag still ends on pointerup.
    }
    const move = (e) => dragMove(e.clientY);
    const up = () => {
      head.removeEventListener("pointermove", move);
      head.removeEventListener("pointerup", up);
      head.removeEventListener("pointercancel", up);
      dragEnd();
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", up);
    head.addEventListener("pointercancel", up);
  });

  body.addEventListener(
    "touchstart",
    (event) => {
      if (!current || event.touches.length > 1) {
        bodyTouch = null;
        return;
      }
      bodyTouch = { y0: event.touches[0].clientY, top: body.scrollTop, on: false };
    },
    { passive: true }
  );

  body.addEventListener(
    "touchmove",
    (event) => {
      const t = bodyTouch;
      if (!t) return;
      const y = event.touches[0].clientY;
      const dy = y - t.y0;
      if (!t.on) {
        if (dy > BODY_SLOP && t.top <= 0 && body.scrollTop <= 0) {
          t.on = true;
          dragStart(y);
        } else {
          if (Math.abs(dy) > BODY_SLOP) bodyTouch = null;
          return;
        }
      }
      event.preventDefault();
      dragMove(y);
    },
    { passive: false }
  );

  const bodyTouchEnd = () => {
    if (bodyTouch?.on) dragEnd();
    bodyTouch = null;
  };
  body.addEventListener("touchend", bodyTouchEnd);
  body.addEventListener("touchcancel", bodyTouchEnd);

  backdrop.addEventListener("click", close);
  root.addEventListener("click", (event) => {
    if (event.target.closest('[data-role="bottom-sheet-close"], [data-bottom-sheet-close]')) close();
  });

  document.addEventListener("keydown", (event) => {
    if (!current) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = [
      ...panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
    ].filter((el) => !el.disabled && el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (!panel.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    }
  });

  window.visualViewport?.addEventListener("resize", () => current && fitToViewport());
  window.addEventListener("resize", () => current && fitToViewport());

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-bottom-sheet]");
    if (!trigger) return;
    const content = document.getElementById(trigger.dataset.bottomSheet);
    if (!content) return;
    event.preventDefault();
    open({ title: content.dataset.bottomSheetTitle, content, opener: trigger });
  });

  window.bottomSheet = { open, close, isOpen: () => current !== null };
})();
