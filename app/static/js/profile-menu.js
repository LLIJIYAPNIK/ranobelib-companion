// Profile dropdown (PR 93) - click the avatar (PR 88) to reveal Профиль/Читаю/Настройки/
// Выйти, closing on a second click, a click outside the menu, or Escape. Same open/close
// mechanics as the custom <select> dropdown (PR 54's custom-dropdown.js) - toggle a class,
// listen for an outside click - but without that one's listbox keyboard navigation, since
// this menu is just a handful of plain links (and one submit button), not selectable
// options.
//
// PR 97: the panel used to be `position: absolute` inside `.profile-menu`, itself inside
// `.sidebar` - and `.sidebar` has `overflow-y: auto` for its own link list's scrolling.
// An absolutely-positioned descendant is still clipped to an ancestor's overflow box no
// matter its z-index, so the menu was visually cut off at the sidebar's edge instead of
// floating above the page. Fixed by portaling the panel out to <body> while open,
// `position: fixed`, positioned from the trigger's own `getBoundingClientRect()` instead
// of CSS relative to a clipping ancestor - moved back to its original spot on close so
// the rest of the markup (and a no-JS fallback) still see it nested where it started.
(() => {
  const GAP = 6;

  const wrapper = document.querySelector('[data-role="profile-menu"]');
  if (!wrapper) return;

  const trigger = wrapper.querySelector('[data-role="profile-menu-trigger"]');
  const panel = wrapper.querySelector('[data-role="profile-menu-panel"]');
  const chevron = trigger.querySelector(".sidebar__account-chevron");
  const homeParent = wrapper;
  const homeNextSibling = panel.nextSibling;

  function isOpen() {
    return wrapper.classList.contains("profile-menu--open");
  }

  // Mirrors the direction the chevron points to whichever side the panel actually opens
  // toward, instead of a breakpoint-hardcoded rotation that only happened to match the
  // typical desktop/mobile layouts (PR 233).
  function setChevronDirection(openBelow) {
    if (!chevron) return;
    chevron.classList.toggle("sidebar__account-chevron--down", openBelow);
    chevron.classList.toggle("sidebar__account-chevron--up", !openBelow);
  }

  // Same "more room below or above" comparison position() uses, minus the panel-height
  // term - the panel measures 0 while parked collapsed inside .sidebar (see app.css), so
  // that term is meaningless before the panel has ever been portaled out for an open().
  // Good enough for the idle chevron's resting direction; position() below still computes
  // the precise, height-aware value once the panel is actually about to be shown.
  function estimateOpenBelow(rect) {
    return window.innerHeight - rect.bottom >= rect.top;
  }

  function updateIdleChevronDirection() {
    if (isOpen()) return;
    setChevronDirection(estimateOpenBelow(trigger.getBoundingClientRect()));
  }

  // Opens toward whichever side of the trigger has more room - upward on the desktop
  // sidebar (the avatar sits at its bottom), downward on the mobile top account strip -
  // without hardcoding either as a breakpoint-driven assumption.
  function position() {
    const rect = trigger.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const openBelow = spaceBelow >= panel.offsetHeight + GAP || spaceBelow >= rect.top;

    panel.classList.toggle("profile-menu__panel--below", openBelow);
    panel.classList.toggle("profile-menu__panel--above", !openBelow);
    setChevronDirection(openBelow);

    if (openBelow) {
      panel.style.top = `${rect.bottom + GAP}px`;
      panel.style.bottom = "auto";
    } else {
      panel.style.bottom = `${window.innerHeight - rect.top + GAP}px`;
      panel.style.top = "auto";
    }

    const left = Math.min(rect.left, window.innerWidth - panel.offsetWidth - GAP);
    panel.style.left = `${Math.max(GAP, left)}px`;
  }

  function open() {
    document.body.appendChild(panel);
    panel.style.position = "fixed";
    position();
    wrapper.classList.add("profile-menu--open");
    // The visual open state lives on the panel itself, not a `.profile-menu--open
    // .profile-menu__panel` descendant selector - now that the panel is a sibling of
    // `.profile-menu` in the DOM (portaled to <body> above) rather than its descendant,
    // a descendant selector would silently never match.
    panel.classList.add("profile-menu__panel--open");
    trigger.setAttribute("aria-expanded", "true");
    window.addEventListener("resize", closeOnLayoutChange);
    window.addEventListener("scroll", closeOnLayoutChange, true);
  }

  function close(refocusTrigger = false) {
    wrapper.classList.remove("profile-menu--open");
    panel.classList.remove("profile-menu__panel--open");
    trigger.setAttribute("aria-expanded", "false");
    window.removeEventListener("resize", closeOnLayoutChange);
    window.removeEventListener("scroll", closeOnLayoutChange, true);
    homeParent.insertBefore(panel, homeNextSibling);
    panel.style.position = "";
    panel.style.top = "";
    panel.style.bottom = "";
    panel.style.left = "";
    if (refocusTrigger) trigger.focus();
  }

  function closeOnLayoutChange() {
    close();
  }

  trigger.addEventListener("click", () => (isOpen() ? close() : open()));

  document.addEventListener("click", (event) => {
    if (isOpen() && !wrapper.contains(event.target) && !panel.contains(event.target)) {
      close();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close(true);
  });

  updateIdleChevronDirection();
  window.addEventListener("resize", updateIdleChevronDirection);
})();
