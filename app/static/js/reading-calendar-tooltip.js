// PR 160: custom tooltip for the reading-activity heatmap on /profile, replacing the
// native `title` attribute the day cells used to carry (PR 136/140/159). The label text
// itself is unchanged - still the "\n"-joined lines app/api/profile.py builds - only how
// it's displayed changes, so this is event-delegation + positioning, same
// getBoundingClientRect()-based approach as profile-menu.js, not a new label format.
(() => {
  const GAP = 8;

  const calendar = document.querySelector('[data-role="reading-calendar"]');
  const tooltip = document.querySelector('[data-role="reading-calendar-tooltip"]');
  if (!calendar || !tooltip) return;

  let activeCell = null;

  function position(cell) {
    const rect = cell.getBoundingClientRect();
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;

    let top = rect.top - th - GAP;
    if (top < GAP) top = rect.bottom + GAP;

    let left = rect.left + rect.width / 2 - tw / 2;
    left = Math.max(GAP, Math.min(left, window.innerWidth - tw - GAP));

    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
  }

  function show(cell) {
    const label = cell.dataset.tooltip;
    if (!label) return;
    activeCell = cell;
    tooltip.textContent = label;
    tooltip.classList.add("reading-calendar-tooltip--open");
    position(cell);
  }

  function hide() {
    activeCell = null;
    tooltip.classList.remove("reading-calendar-tooltip--open");
  }

  // pointerover/pointerout, not mouseover/mouseout: a tap dispatches a native
  // `pointerover` too (pointerType "touch"), but browsers additionally synthesize a
  // legacy `mouseover` right before their synthesized `click` for touch/compat reasons -
  // if that legacy event opened the tooltip, this same cell's click handler below would
  // immediately read it as already-open and toggle it back closed on every single tap.
  // Checking pointerType here (mouse only) keeps this handler out of the tap's own
  // opening path entirely, since only PointerEvent carries pointerType - the synthesized
  // MouseEvent never reaches this listener at all.
  calendar.addEventListener("pointerover", (event) => {
    if (event.pointerType !== "mouse") return;
    const cell = event.target.closest(".reading-calendar__day");
    if (cell && cell !== activeCell) show(cell);
  });

  calendar.addEventListener("pointerout", (event) => {
    if (event.pointerType !== "mouse") return;
    const cell = event.target.closest(".reading-calendar__day");
    if (cell && !cell.contains(event.relatedTarget)) hide();
  });

  // Touch's own tap has no hover ahead of it, so it needs this click handler as its only
  // way to reach show()/hide() - reusing the exact same functions, not a second
  // implementation of the same positioning. On mouse, pointerover above already handles
  // open/close, so this stays a no-op there (see that handler's own comment on why a
  // pointer-type-unaware click handler would fight it).
  let lastPointerType = "mouse";
  calendar.addEventListener("pointerdown", (event) => {
    lastPointerType = event.pointerType;
  });

  calendar.addEventListener("click", (event) => {
    if (lastPointerType === "mouse") return;
    const cell = event.target.closest(".reading-calendar__day");
    if (!cell) return;
    if (cell === activeCell) hide();
    else show(cell);
  });

  // A hovered cell's own on-screen position goes stale on scroll/resize (the tooltip
  // isn't re-measured continuously) - closing it here matches how the other floating
  // panels in this app (profile-menu.js) react to the same layout changes.
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
})();
